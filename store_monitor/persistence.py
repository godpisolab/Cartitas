"""Persistencia en PostgreSQL del resultado de un scraping (bloque B de
cambios-necesarios-scraper.md). Ya no es un hook opcional como el
`price_history.py` mencionado en el README original -- es parte central del
flujo de main(), no un añadido que se pueda omitir en silencio si falta.

Dos escrituras separadas, cada una descrita en detalle junto a su función:

1. sync_stores()          -- STORES (código) -> tabla `store`, UPSERT por
                              website_url (B.1). Solo toca los campos
                              ESTÁTICOS que existen en StoreConfig.
2. persist_scrape_results() -- productos ya scrapeados -> store_product +
                              price_history + restock_event, una transacción
                              POR TIENDA, no por producto (B.3/B.4).
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date
from typing import Optional

import psycopg2
import psycopg2.extras

from shared.domain import Product, RefreshOutcome, StoreConfig
from scrapers import SCRAPER_CLASSES

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    # Puerto 5433, no el 5432 por defecto de Postgres -- ver docker-compose.yml
    # (el 5432 del host ya lo usa un Postgres del sistema ajeno a este proyecto).
    "postgresql://cartitas:cartitas@localhost:5433/cartitas",
)

# D.4: el scraper interno usa mayúsculas (DISPONIBLE/AGOTADO/DESCONOCIDO) --
# esta es la ÚNICA capa de traducción a los valores exactos del enum de
# Postgres (minúscula), aplicada justo en el punto de escritura. No hace
# falta tocar el código interno del scraper para esto.
_STOCK_STATUS_MAP = {
    "DISPONIBLE": "disponible",
    "AGOTADO": "agotado",
    "DESCONOCIDO": "desconocido",
}


def normalize_stock_status(raw: str | None) -> str:
    return _STOCK_STATUS_MAP.get((raw or "").upper(), "desconocido")


def _truncate(value: str | None, max_length: int, *, field: str, context: str) -> str | None:
    """Los VARCHAR del esquema tienen un límite (raw_name 500, raw_variant y
    store_sku 255) -- un scraper de una tienda con HTML inesperado puede
    colar texto mucho más largo de lo normal (visto en Arte9: un selector
    CSS demasiado amplio capturó varios elementos hermanos como si fueran el
    título). Se trunca con aviso en vez de dejar que UN producto corrupto
    tire abajo la transacción entera de la tienda (B.3: la atomicidad es
    para no dejar escrituras a medias, no para que un dato sucio bloquee 67
    productos buenos)."""
    if value is None or len(value) <= max_length:
        return value
    print(f"[persistencia] AVISO: {field} de {max_length}+ caracteres truncado ({context})")
    return value[:max_length]


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def sync_stores(conn, stores: list[StoreConfig]) -> dict[str, int]:
    """B.1: UPSERT de STORES (código) -> tabla `store`, por website_url (la
    clave estable). Solo actualiza name/platform -- los únicos campos
    ESTÁTICOS que existen en StoreConfig. Todo lo demás en `store`
    (sitemap_url, has_structured_api, api_endpoint, y los DINÁMICOS
    crawl_delay_seconds/backoff_until/last_scraped_at/robots_checked_at) no
    tiene representación en código y esta sincronización nunca lo toca --
    vive y se gestiona solo en la BBDD.

    Devuelve {website_url: store_id} para el resto de la persistencia."""
    rows = [(s.label, s.domain, s.platform.value) for s in stores]

    with conn.cursor() as cur:
        result = psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO store (name, website_url, platform)
            VALUES %s
            ON CONFLICT (website_url) DO UPDATE SET
                name = EXCLUDED.name,
                platform = EXCLUDED.platform
            RETURNING id, website_url
            """,
            rows,
            fetch=True,
        )
    return {website_url: store_id for store_id, website_url in result}


def _save_one_store(conn, store_id: int, products: list[Product], scraped_date: date,
                     *, etag: Optional[str] = None, last_modified: Optional[str] = None) -> list[int]:
    """B.3: store_product + price_history + restock_event de UNA tienda en
    una única transacción (el llamador hace commit/rollback). B.4: todos los
    productos de la tienda en un único INSERT ... ON CONFLICT por lotes
    (execute_values), no fila a fila.

    `etag`/`last_modified` (A.4): SOLO tienen sentido cuando `products` viene
    del refresco individual de UN producto (E.2, ver refresh_hot_products) --
    en ese caso todas las filas de esta llamada comparten la misma URL, así
    que el mismo ETag/Last-Modified de la respuesta aplica a todas. El
    barrido diario completo nunca los pasa (A.4: no aplica ahí).

    Devuelve los `id` de los restock_event creados en esta llamada -- E.3
    los usa para disparar notificaciones justo después, sin tener que
    volver a consultar qué es "nuevo"."""
    valid = [p for p in products if p.url and p.name]
    skipped = len(products) - len(valid)
    if skipped:
        print(f"[persistencia] AVISO: {skipped} producto(s) sin url/name descartados (store_id={store_id})")
    if not valid:
        return []

    store_urls = [p.url for p in valid]

    with conn.cursor() as cur:
        # B.2: estado ANTERIOR, leído ANTES de sobrescribirlo -- imprescindible
        # para detectar la transición agotado -> disponible. Clave (store_url,
        # raw_variant), NO solo store_url: varias variantes de un mismo
        # producto Shopify (idioma, sobre/caja...) comparten store_url con
        # stock independiente por variante (caso Pokemillon, D.5) -- si se
        # clave solo por store_url, la transición de una variante se
        # confundiría con la de otra. Un (store_url, raw_variant) sin fila
        # previa (producto/variante nueva) no puede ser un restock: no hay
        # nadie que pudiera estar suscrito a algo que no existía todavía.
        cur.execute(
            "SELECT store_url, raw_variant, stock_status FROM store_product WHERE store_id = %s AND store_url = ANY(%s)",
            (store_id, store_urls),
        )
        previous_status = {(url, variant): status for url, variant, status in cur.fetchall()}

        upsert_rows = [
            (
                store_id,
                p.url,
                _truncate(p.sku, 255, field="store_sku", context=p.url),
                _truncate(p.name, 500, field="raw_name", context=p.url),
                _truncate(p.variant, 255, field="raw_variant", context=p.url),
                p.tags,  # TEXT, sin límite -- solo Shopify lo rellena (ver Product.tags)
                p.price,
                normalize_stock_status(p.stock_status),
                etag,
                last_modified,
            )
            for p in valid
        ]
        upserted = psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO store_product
                (store_id, store_url, store_sku, raw_name, raw_variant, raw_tags, current_price, stock_status,
                 last_etag, last_modified_header, last_checked_at)
            VALUES %s
            ON CONFLICT (store_id, store_url, raw_variant) DO UPDATE SET
                store_sku = EXCLUDED.store_sku,
                raw_name = EXCLUDED.raw_name,
                raw_tags = EXCLUDED.raw_tags,
                current_price = EXCLUDED.current_price,
                stock_status = EXCLUDED.stock_status,
                last_etag = EXCLUDED.last_etag,
                last_modified_header = EXCLUDED.last_modified_header,
                last_checked_at = EXCLUDED.last_checked_at
            RETURNING id, store_url, raw_variant, product_id, current_price, stock_status
            """,
            upsert_rows,
            template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())",
            fetch=True,
        )

        price_rows = [
            (store_product_id, price, stock_status, scraped_date)
            for store_product_id, _url, _variant, _product_id, price, stock_status in upserted
        ]
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO price_history (store_product_id, price, stock_status, scraped_date)
            VALUES %s
            ON CONFLICT (store_product_id, scraped_date) DO UPDATE SET
                price = EXCLUDED.price,
                stock_status = EXCLUDED.stock_status
            """,
            price_rows,
        )

        # B.2: solo cuenta como restock si el anterior era 'agotado' Y el
        # nuevo es 'disponible'. 'desconocido' -> 'disponible' NO cuenta
        # (decidido 2026-08-26): no sabíamos el estado real, así que "ahora
        # disponible" no es necesariamente una novedad. Y sin product_id
        # (todavía no hay match confirmado -- bloque C, no construido aún)
        # no hay un producto canónico contra el que registrar el evento.
        restock_rows = [
            (store_product_id, product_id)
            for store_product_id, url, variant, product_id, _price, new_status in upserted
            if product_id and previous_status.get((url, variant)) == "agotado" and new_status == "disponible"
        ]
        if not restock_rows:
            return []

        restock_event_ids = psycopg2.extras.execute_values(
            cur,
            "INSERT INTO restock_event (store_product_id, product_id) VALUES %s RETURNING id",
            restock_rows,
            fetch=True,
        )
        print(f"[persistencia] {len(restock_rows)} restock(s) detectado(s) (store_id={store_id})")
        return [row[0] for row in restock_event_ids]


def persist_scrape_results(products: list[Product], stores: list[StoreConfig]) -> list[int]:
    """Punto de entrada llamado desde main(). Sincroniza STORES -> `store`, y
    luego guarda cada tienda en su PROPIA transacción (B.4): un fallo en una
    tienda concreta (dato corrupto, constraint violada) no debe impedir que
    se guarden las demás -- mismo principio de aislamiento por tienda que ya
    usa el scraping en sí (ver StoreQueryResult).

    Devuelve los `id` de TODOS los restock_event creados en esta pasada
    (todas las tiendas) -- E.3 los usa para disparar notificaciones."""
    products_by_store: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        products_by_store[p.store].append(p)

    domain_by_label = {s.label: s.domain for s in stores}
    scraped_date = date.today()
    all_restock_event_ids: list[int] = []

    conn = get_connection()
    try:
        store_ids = sync_stores(conn, stores)
        conn.commit()

        saved, failed = 0, 0
        for label, store_products in products_by_store.items():
            domain = domain_by_label.get(label)
            store_id = store_ids.get(domain) if domain else None
            if store_id is None:
                print(f"[persistencia] AVISO: '{label}' no está en la tabla store tras el sync, se omite")
                failed += 1
                continue
            try:
                all_restock_event_ids.extend(_save_one_store(conn, store_id, store_products, scraped_date))
                # store.last_scraped_at: SOLO aquí, no dentro de _save_one_store --
                # esa función también la usan refresh_hot_products (E.2) y
                # sitemap_poller (E.1), y last_scraped_at debe reflejar el
                # barrido completo por categoría, no un refresco puntual.
                with conn.cursor() as cur:
                    cur.execute("UPDATE store SET last_scraped_at = now() WHERE id = %s", (store_id,))
                conn.commit()
                saved += 1
            except Exception as e:
                conn.rollback()
                print(f"[persistencia] ERROR guardando '{label}': {type(e).__name__}: {e}")
                failed += 1

        print(f"[persistencia] {saved}/{saved + failed} tiendas guardadas en Postgres")
    finally:
        conn.close()

    return all_restock_event_ids


def refresh_hot_products(conn, stores: list[StoreConfig]) -> tuple[dict, list[int]]:
    """E.2: refresca cada store_product cuyo producto canónico esté marcado
    is_hot (y no caducado) -- consulta exacta de cambios-necesarios-scraper.md.

    Reutiliza _save_one_store en vez de duplicar la lógica de detección de
    restock/price_history: se reconstruyen `Product` "sintéticos" con lo YA
    CONOCIDO (raw_name/raw_variant/store_sku, tal como están guardados)
    combinado con el precio/stock recién leído -- un refresco no redescubre
    el producto, solo comprueba si cambió (ver la limitación explícita de
    modelo-datos-app-tcg.md punto 4: esto NUNCA descubre listados nuevos,
    solo vigila los que ya se conocían).

    Devuelve (recuento por status, ids de restock_event creados) -- lo
    segundo para que E.3 dispare notificaciones justo después."""
    domain_to_config = {s.domain: s for s in stores}

    with conn.cursor() as cur:
        cur.execute("""
            SELECT sp.store_id, s.website_url, sp.store_url, sp.raw_variant, sp.raw_name,
                   sp.store_sku, sp.last_etag, sp.last_modified_header
            FROM store_product sp
            JOIN store s ON s.id = sp.store_id
            JOIN product p ON p.id = sp.product_id
            WHERE p.is_hot = true AND (p.hot_until IS NULL OR p.hot_until >= CURRENT_DATE)
            ORDER BY sp.store_id, sp.store_url
        """)
        rows = cur.fetchall()

    # Agrupar por (store_id, website_url, store_url): Shopify puede tener
    # varias filas (variantes) para la misma URL -- se pide una sola vez y
    # el resultado se reparte entre todas sus filas (ver refresh_product).
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for store_id, website_url, store_url, raw_variant, raw_name, store_sku, last_etag, last_modified in rows:
        groups[(store_id, website_url, store_url)].append({
            "raw_variant": raw_variant, "raw_name": raw_name, "store_sku": store_sku,
            "last_etag": last_etag, "last_modified": last_modified,
        })

    counts = {"modified": 0, "not_modified": 0, "error": 0, "not_supported": 0}
    restock_event_ids: list[int] = []
    scraped_date = date.today()

    for (store_id, website_url, store_url), variant_rows in groups.items():
        config = domain_to_config.get(website_url)
        if config is None:
            counts["error"] += 1
            continue

        scraper_cls = SCRAPER_CLASSES[config.platform]
        first = variant_rows[0]
        try:
            outcome: RefreshOutcome = scraper_cls.refresh_product(
                config, store_url, store_sku=first["store_sku"],
                etag=first["last_etag"], last_modified=first["last_modified"],
            )
        except Exception as e:
            print(f"[hot-refresh] ERROR en {store_url}: {type(e).__name__}: {e}")
            counts["error"] += 1
            continue

        if outcome.status in ("not_supported", "error"):
            if outcome.status == "error":
                print(f"[hot-refresh] ERROR en {store_url}: {outcome.error}")
            counts[outcome.status] += 1
            continue

        if outcome.status == "not_modified":
            # A.4: 304 -- barato, solo se actualiza last_checked_at.
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE store_product SET last_checked_at = now() WHERE store_id = %s AND store_url = %s",
                    (store_id, store_url),
                )
            conn.commit()
            counts["not_modified"] += 1
            continue

        synthetic_products = []
        for variant_row in variant_rows:
            matched = next((v for v in outcome.variants if v.variant == variant_row["raw_variant"]), None)
            if matched is None:
                continue
            synthetic_products.append(Product(
                store=config.label, platform=config.platform.value,
                id_product=None, name=variant_row["raw_name"], variant=variant_row["raw_variant"],
                product_type="", main_set=None, set_code=None, language=None,
                price=matched.price, stock_status=matched.stock_status,
                url=store_url, sku=variant_row["store_sku"], image_url=None,
            ))

        if not synthetic_products:
            counts["error"] += 1
            continue

        try:
            new_restock_ids = _save_one_store(
                conn, store_id, synthetic_products, scraped_date,
                etag=outcome.etag, last_modified=outcome.last_modified,
            )
            conn.commit()
            restock_event_ids.extend(new_restock_ids)
            counts["modified"] += 1
        except Exception as e:
            conn.rollback()
            print(f"[hot-refresh] ERROR guardando {store_url}: {type(e).__name__}: {e}")
            counts["error"] += 1

    print(f"[hot-refresh] {counts}")
    return counts, restock_event_ids

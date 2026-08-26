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

import psycopg2
import psycopg2.extras

from base_script import Product, StoreConfig

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


def _save_one_store(conn, store_id: int, products: list[Product], scraped_date: date) -> None:
    """B.3: store_product + price_history + restock_event de UNA tienda en
    una única transacción (el llamador hace commit/rollback). B.4: todos los
    productos de la tienda en un único INSERT ... ON CONFLICT por lotes
    (execute_values), no fila a fila."""
    valid = [p for p in products if p.url and p.name]
    skipped = len(products) - len(valid)
    if skipped:
        print(f"[persistencia] AVISO: {skipped} producto(s) sin url/name descartados (store_id={store_id})")
    if not valid:
        return

    store_urls = [p.url for p in valid]

    with conn.cursor() as cur:
        # B.2: estado ANTERIOR, leído ANTES de sobrescribirlo -- imprescindible
        # para detectar la transición agotado -> disponible. Un store_url sin
        # fila previa (producto nuevo) no puede ser un restock: no hay nadie
        # que pudiera estar suscrito a algo que no existía todavía.
        cur.execute(
            "SELECT store_url, stock_status FROM store_product WHERE store_id = %s AND store_url = ANY(%s)",
            (store_id, store_urls),
        )
        previous_status = dict(cur.fetchall())

        upsert_rows = [
            (
                store_id,
                p.url,
                _truncate(p.sku, 255, field="store_sku", context=p.url),
                _truncate(p.name, 500, field="raw_name", context=p.url),
                _truncate(p.variant, 255, field="raw_variant", context=p.url),
                p.price,
                normalize_stock_status(p.stock_status),
            )
            for p in valid
        ]
        upserted = psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO store_product
                (store_id, store_url, store_sku, raw_name, raw_variant, current_price, stock_status, last_checked_at)
            VALUES %s
            ON CONFLICT (store_id, store_url) DO UPDATE SET
                store_sku = EXCLUDED.store_sku,
                raw_name = EXCLUDED.raw_name,
                raw_variant = EXCLUDED.raw_variant,
                current_price = EXCLUDED.current_price,
                stock_status = EXCLUDED.stock_status,
                last_checked_at = EXCLUDED.last_checked_at
            RETURNING id, store_url, product_id, current_price, stock_status
            """,
            upsert_rows,
            template="(%s, %s, %s, %s, %s, %s, %s, now())",
            fetch=True,
        )

        price_rows = [
            (store_product_id, price, stock_status, scraped_date)
            for store_product_id, _url, _product_id, price, stock_status in upserted
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
            for store_product_id, url, product_id, _price, new_status in upserted
            if product_id and previous_status.get(url) == "agotado" and new_status == "disponible"
        ]
        if restock_rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO restock_event (store_product_id, product_id) VALUES %s",
                restock_rows,
            )
            print(f"[persistencia] {len(restock_rows)} restock(s) detectado(s) (store_id={store_id})")


def persist_scrape_results(products: list[Product], stores: list[StoreConfig]) -> None:
    """Punto de entrada llamado desde main(). Sincroniza STORES -> `store`, y
    luego guarda cada tienda en su PROPIA transacción (B.4): un fallo en una
    tienda concreta (dato corrupto, constraint violada) no debe impedir que
    se guarden las demás -- mismo principio de aislamiento por tienda que ya
    usa el scraping en sí (ver StoreQueryResult)."""
    products_by_store: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        products_by_store[p.store].append(p)

    domain_by_label = {s.label: s.domain for s in stores}
    scraped_date = date.today()

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
                _save_one_store(conn, store_id, store_products, scraped_date)
                conn.commit()
                saved += 1
            except Exception as e:
                conn.rollback()
                print(f"[persistencia] ERROR guardando '{label}': {type(e).__name__}: {e}")
                failed += 1

        print(f"[persistencia] {saved}/{saved + failed} tiendas guardadas en Postgres")
    finally:
        conn.close()

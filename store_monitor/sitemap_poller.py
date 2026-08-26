"""Polling de sitemap.xml (E.1) -- mecanismo de descubrimiento TEMPRANO de
producto nuevo, descrito en modelo-datos-app-tcg.md punto 5. Compara las
URLs <loc> del sitemap de una tienda contra las ya conocidas en
store_product.store_url; cuando aparece una URL nueva, dispara una
extracción puntual solo de esa ficha -- reutilizando
BaseStoreScraper.refresh_product(), la misma pieza que ya usa el refresco
de calientes (E.2), en vez de duplicar lógica de parseo por plataforma.

Requiere `store.sitemap_url` poblado A MANO por tienda -- no viene de
STORES/código (B.1: sync_stores() nunca toca este campo, no tiene
representación en StoreConfig). Sin sitemap_url, una tienda simplemente no
participa en este mecanismo y se limita al barrido diario -- degradación
esperada, no un error:

    UPDATE store SET sitemap_url = 'https://midominio.com/sitemap.xml' WHERE name = 'Mi Tienda';

LIMITACIÓN explícita (ver modelo-datos-app-tcg.md): esto NUNCA descubre que
una tienda que antes no vendía un producto ha empezado a venderlo -- solo
detecta URLs de producto que aparecen por primera vez en el sitemap. Eso
solo lo cubre el barrido diario por categoría.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

import persistence
from domain import Product, StoreConfig
from http_client import build_session, request_with_retries
from scrapers import SCRAPER_CLASSES

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# El sitemap de una tienda cubre TODO su catálogo, no solo la categoría de
# One Piece que scrapeamos (verificado en Cardzone: ~4100 URLs de producto
# en total, de varios TCGs) -- filtrar por prefijo de ruta conocido no
# basta, porque "/products/" es compartido por todo el catálogo Shopify.
# Como este proyecto scrapea un único juego (ver README), se filtra además
# por esta palabra en el slug de la URL -- heurística específica de este
# proyecto, no una solución genérica (si algún día se scrapean varios
# juegos por tienda, esto necesitará parametrizarse por StoreConfig).
_GAME_URL_KEYWORDS = ("one-piece", "one_piece", "onepiece")

# Tope de seguridad: aunque el filtrado de arriba falle o una tienda añada
# de golpe muchos productos nuevos, no se procesan más de esto por ciclo --
# cada URL nueva es una petición de red real (vía refresh_product). Lo que
# quede pendiente se recoge en el siguiente ciclo de polling.
MAX_NEW_URLS_PER_POLL = 30


def _fetch_locs(session, url: str) -> list[str]:
    resp = request_with_retries(session, url)
    if resp is None or resp.status_code != 200:
        return []
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []
    return [el.text for el in root.findall(".//sm:loc", _SITEMAP_NS) if el.text], root.tag


def _fetch_sitemap_urls(session, sitemap_url: str) -> list[str]:
    """Descarga y parsea sitemap_url. Si es un sitemap ÍNDICE (apunta a
    otros sitemaps, patrón habitual en WooCommerce/Shopify cuando el
    catálogo es grande), sigue UN nivel de indirección -- suficiente para
    las plataformas soportadas, que no anidan más profundo.

    Verificado contra Cardzone (Shopify): el índice separa productos,
    colecciones y páginas en sitemaps DISTINTOS
    (sitemap_products_1.xml, sitemap_pages_1.xml...) -- sin filtrar, esto
    descargaría y comparar miles de URLs de páginas/colecciones totalmente
    irrelevantes. Solo se siguen los sub-sitemaps cuyo nombre sugiere
    "product"; el resto ni se descarga."""
    result = _fetch_locs(session, sitemap_url)
    if not result:
        return []
    locs, root_tag = result

    if not root_tag.endswith("sitemapindex"):
        return locs

    all_urls: list[str] = []
    for sub_sitemap in locs:
        if "product" not in sub_sitemap.lower():
            continue
        sub_result = _fetch_locs(session, sub_sitemap)
        if sub_result:
            all_urls.extend(sub_result[0])
    return all_urls


def poll_sitemaps(conn, stores: list[StoreConfig]) -> dict:
    """Para cada tienda con `sitemap_url` en BBDD: descubre URLs nuevas y
    las extrae puntualmente, guardándolas en store_product igual que
    cualquier alta (vía persistence._save_one_store)."""
    config_by_domain = {s.domain: s for s in stores}

    with conn.cursor() as cur:
        cur.execute("SELECT id, website_url, sitemap_url FROM store WHERE sitemap_url IS NOT NULL")
        store_rows = cur.fetchall()

    if not store_rows:
        print("[sitemap] 0 tiendas con sitemap_url configurado -- nada que comprobar "
              "(ver docstring de sitemap_poller.py para activarlo por tienda)")
        return {"tiendas_comprobadas": 0, "urls_nuevas": 0}

    scraped_date = date.today()
    total_new = 0
    checked = 0

    for store_id, website_url, sitemap_url in store_rows:
        config = config_by_domain.get(website_url)
        if config is None:
            print(f"[sitemap] AVISO: {website_url} tiene sitemap_url pero no está en STORES, se omite")
            continue
        checked += 1

        session = build_session(anti_bot=False, config=config)
        sitemap_urls = set(_fetch_sitemap_urls(session, sitemap_url))
        if not sitemap_urls:
            print(f"[sitemap] AVISO: {config.label} -- sitemap vacío o no accesible ({sitemap_url})")
            continue

        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT store_url FROM store_product WHERE store_id = %s", (store_id,))
            known_urls = {row[0] for row in cur.fetchall()}

        # Segunda capa de filtrado (además de _fetch_sitemap_urls): en
        # plataformas que NO separan productos/páginas en sitemaps distintos
        # (a diferencia de Shopify), se acota a las URLs que comparten el
        # mismo prefijo de ruta que los productos YA conocidos de esta
        # tienda -- evita comparar/procesar páginas, categorías, etc. Sin
        # productos conocidos todavía (tienda recién añadida), no hay
        # prefijo que derivar y se sigue sin filtrar por prefijo.
        prefixes = {"/".join(u.split("/")[:4]) + "/" for u in known_urls if len(u.split("/")) > 4}
        if prefixes:
            sitemap_urls = {u for u in sitemap_urls if any(u.startswith(p) for p in prefixes)}

        # Tercera capa: el prefijo de ruta ("/products/") suele ser
        # compartido por TODO el catálogo de la tienda, no solo el juego que
        # nos interesa -- se filtra además por palabra clave del juego en el
        # slug (ver _GAME_URL_KEYWORDS).
        sitemap_urls = {u for u in sitemap_urls if any(kw in u.lower() for kw in _GAME_URL_KEYWORDS)}

        new_urls = sitemap_urls - known_urls
        if not new_urls:
            continue

        if len(new_urls) > MAX_NEW_URLS_PER_POLL:
            print(f"[sitemap] AVISO: {config.label} tiene {len(new_urls)} URL(s) nueva(s), "
                  f"se procesan solo {MAX_NEW_URLS_PER_POLL} este ciclo (el resto, en el siguiente)")
            new_urls = set(list(new_urls)[:MAX_NEW_URLS_PER_POLL])

        print(f"[sitemap] {config.label}: {len(new_urls)} URL(s) nueva(s) en el sitemap, extrayendo...")
        scraper_cls = SCRAPER_CLASSES[config.platform]

        new_products: list[Product] = []
        for url in new_urls:
            outcome = scraper_cls.refresh_product(config, url)
            if outcome.status != "modified":
                continue
            for variant in outcome.variants:
                if not variant.name:
                    continue  # sin nombre no hay nada útil que guardar (ver RefreshedVariant.name)
                new_products.append(Product(
                    store=config.label, platform=config.platform.value,
                    id_product=None, name=variant.name, variant=variant.variant,
                    product_type="", main_set=None, set_code=None, language=None,
                    price=variant.price, stock_status=variant.stock_status,
                    url=url, sku=None, image_url=None,
                ))

        if not new_products:
            continue

        try:
            persistence._save_one_store(conn, store_id, new_products, scraped_date)
            conn.commit()
            total_new += len(new_products)
        except Exception as e:
            conn.rollback()
            print(f"[sitemap] ERROR guardando altas nuevas de {config.label}: {type(e).__name__}: {e}")

    print(f"[sitemap] {checked} tienda(s) comprobada(s), {total_new} producto(s) nuevo(s) dado(s) de alta")
    return {"tiendas_comprobadas": checked, "urls_nuevas": total_new}

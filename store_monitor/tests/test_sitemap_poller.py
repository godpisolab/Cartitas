"""Tests de sitemap_poller.py -- sin cobertura hasta ahora (bloque E).

OJO al escribir tests aquí: durante el desarrollo real, un mock con una
lista de respuestas más corta que las páginas que el código pedía provocó
un bucle infinito real (requests-mock repite la última respuesta para
siempre). Todos los mocks de este fichero son deliberadamente acotados:
_fetch_sitemap_urls se prueba con sitemaps ÚNICOS (sin índice que
recorrer indefinidamente), y poll_sitemaps con listados de <url> FINITOS."""

from __future__ import annotations

from datetime import date

import pytest

import persistence
import sitemap_poller
from shared.domain import Platform, Product, RefreshedVariant, RefreshOutcome, StoreConfig


def sitemap_xml(urls: list[str]) -> str:
    locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'


def sitemap_index_xml(sub_sitemaps: list[str]) -> str:
    locs = "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in sub_sitemaps)
    return f'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</sitemapindex>'


class TestFetchSitemapUrls:
    def test_sitemap_plano_sin_indice_devuelve_las_urls_directas(self, requests_mock):
        import requests
        requests_mock.get(
            "https://tienda.example/sitemap.xml",
            text=sitemap_xml(["https://tienda.example/products/a", "https://tienda.example/products/b"]),
        )
        urls = sitemap_poller._fetch_sitemap_urls(requests.Session(), "https://tienda.example/sitemap.xml")
        assert set(urls) == {"https://tienda.example/products/a", "https://tienda.example/products/b"}

    def test_sitemap_indice_solo_sigue_sub_sitemaps_con_product_en_el_nombre(self, requests_mock):
        import requests
        requests_mock.get(
            "https://tienda.example/sitemap.xml",
            text=sitemap_index_xml([
                "https://tienda.example/sitemap_products_1.xml",
                "https://tienda.example/sitemap_pages_1.xml",
            ]),
        )
        requests_mock.get(
            "https://tienda.example/sitemap_products_1.xml",
            text=sitemap_xml(["https://tienda.example/products/a"]),
        )
        # OJO: si el código intentara descargar sitemap_pages_1.xml, este
        # test fallaría por falta de mock registrado para esa URL --
        # confirma que el filtro por "product" evita esa descarga.
        urls = sitemap_poller._fetch_sitemap_urls(requests.Session(), "https://tienda.example/sitemap.xml")
        assert urls == ["https://tienda.example/products/a"]

    def test_sitemap_no_accesible_devuelve_lista_vacia(self, requests_mock):
        import requests
        requests_mock.get("https://tienda.example/sitemap.xml", status_code=500)
        urls = sitemap_poller._fetch_sitemap_urls(requests.Session(), "https://tienda.example/sitemap.xml")
        assert urls == []

    def test_xml_invalido_no_revienta(self, requests_mock):
        import requests
        requests_mock.get("https://tienda.example/sitemap.xml", text="esto no es xml")
        urls = sitemap_poller._fetch_sitemap_urls(requests.Session(), "https://tienda.example/sitemap.xml")
        assert urls == []


def make_config(label="Tienda", domain="https://tienda.example"):
    return StoreConfig(label, domain, Platform.SHOPIFY, shopify_collection="one-piece")


class TestPollSitemaps:
    def test_sin_tiendas_con_sitemap_url_no_hace_nada(self, db_conn):
        result = sitemap_poller.poll_sitemaps(db_conn, [make_config()])
        assert result == {"tiendas_comprobadas": 0, "urls_nuevas": 0}

    def test_tienda_con_sitemap_pero_no_en_stores_se_omite(self, db_conn):
        # La fila de `store` (website_url="https://desconocida.example")
        # tiene sitemap_url, pero el STORES que se pasa a poll_sitemaps solo
        # trae la config de OTRA tienda -- config_by_domain no encuentra
        # "https://desconocida.example" y debe omitirla, no reventar.
        persistence.sync_stores(db_conn, [make_config(domain="https://desconocida.example")])
        db_conn.commit()
        with db_conn.cursor() as cur:
            cur.execute("UPDATE store SET sitemap_url = 'https://desconocida.example/sitemap.xml' "
                        "WHERE website_url = 'https://desconocida.example'")
        db_conn.commit()

        result = sitemap_poller.poll_sitemaps(db_conn, [make_config(domain="https://tienda.example")])
        assert result["tiendas_comprobadas"] == 0

    def test_url_nueva_relevante_se_extrae_y_persiste(self, db_conn, requests_mock, monkeypatch):
        cfg = make_config()
        persistence.sync_stores(db_conn, [cfg])
        db_conn.commit()
        with db_conn.cursor() as cur:
            cur.execute("UPDATE store SET sitemap_url = 'https://tienda.example/sitemap.xml'")
        db_conn.commit()

        # Un producto YA conocido (para derivar el prefijo "/products/") +
        # una URL nueva relevante (contiene "one-piece").
        requests_mock.get(
            "https://tienda.example/sitemap.xml",
            text=sitemap_xml([
                "https://tienda.example/products/ya-conocido",
                "https://tienda.example/products/one-piece-nuevo",
            ]),
        )
        persistence._save_one_store(
            db_conn, list(persistence.sync_stores(db_conn, [cfg]).values())[0],
            [Product(store="Tienda", platform="shopify", id_product=None, name="Ya conocido", variant=None,
                     product_type="", main_set=None, set_code=None, language=None, price=1.0,
                     stock_status="DISPONIBLE", url="https://tienda.example/products/ya-conocido",
                     sku=None, image_url=None)],
            date.today(),
        )
        db_conn.commit()

        fake_outcome = RefreshOutcome(
            status="modified",
            variants=[RefreshedVariant(variant="Default Title", price=25.0, stock_status="DISPONIBLE",
                                        name="Producto Nuevo One Piece")],
        )
        from scrapers.shopify import ShopifyScraper
        monkeypatch.setattr(ShopifyScraper, "refresh_product", classmethod(lambda cls, *a, **kw: fake_outcome))

        result = sitemap_poller.poll_sitemaps(db_conn, [cfg])

        assert result["tiendas_comprobadas"] == 1
        assert result["urls_nuevas"] == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT raw_name FROM store_product WHERE store_url = %s",
                        ("https://tienda.example/products/one-piece-nuevo",))
            assert cur.fetchone()[0] == "Producto Nuevo One Piece"

    def test_url_no_relevante_al_juego_se_ignora(self, db_conn, requests_mock):
        cfg = make_config()
        persistence.sync_stores(db_conn, [cfg])
        db_conn.commit()
        with db_conn.cursor() as cur:
            cur.execute("UPDATE store SET sitemap_url = 'https://tienda.example/sitemap.xml'")
        db_conn.commit()

        # Solo productos de OTRO juego (sin "one-piece" en el slug) -- el
        # filtro de _GAME_URL_KEYWORDS debe descartarlos a todos.
        requests_mock.get(
            "https://tienda.example/sitemap.xml",
            text=sitemap_xml(["https://tienda.example/products/pokemon-booster"]),
        )
        result = sitemap_poller.poll_sitemaps(db_conn, [cfg])
        assert result["urls_nuevas"] == 0

    def test_tope_max_new_urls_per_poll_se_respeta(self, db_conn, requests_mock, monkeypatch):
        cfg = make_config()
        persistence.sync_stores(db_conn, [cfg])
        db_conn.commit()
        with db_conn.cursor() as cur:
            cur.execute("UPDATE store SET sitemap_url = 'https://tienda.example/sitemap.xml'")
        db_conn.commit()

        monkeypatch.setattr(sitemap_poller, "MAX_NEW_URLS_PER_POLL", 3)
        # 10 URLs nuevas, todas relevantes -- sin ningún producto conocido
        # todavía, así que no hay prefijo que derivar (se procesan todas las
        # relevantes por keyword, acotadas por el tope).
        urls = [f"https://tienda.example/one-piece-{i}" for i in range(10)]
        requests_mock.get("https://tienda.example/sitemap.xml", text=sitemap_xml(urls))

        fake_outcome = RefreshOutcome(
            status="modified",
            variants=[RefreshedVariant(variant="Default Title", price=10.0, stock_status="DISPONIBLE",
                                        name="Producto")],
        )
        from scrapers.shopify import ShopifyScraper
        monkeypatch.setattr(ShopifyScraper, "refresh_product", classmethod(lambda cls, *a, **kw: fake_outcome))

        result = sitemap_poller.poll_sitemaps(db_conn, [cfg])

        assert result["urls_nuevas"] == 3  # acotado por el tope parcheado, no las 10 disponibles

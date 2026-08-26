"""Tests de GenericJsonLdScraper -- sección 2.6 del plan de pruebas."""

from __future__ import annotations

import pytest

from domain import Platform, StoreConfig
from http_client import StoreLogger
from scrapers.generic_jsonld import GenericJsonLdScraper


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import scrapers.generic_jsonld as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


def make_scraper(listing_urls=("https://tienda.example/categoria",)):
    cfg = StoreConfig("Tienda", "https://tienda.example", Platform.GENERIC_JSONLD,
                       jsonld_listing_urls=listing_urls,
                       jsonld_product_link_selector="a.producto")
    logger = StoreLogger("Tienda", {})
    return GenericJsonLdScraper(cfg, logger, delay=0)


class TestParseProductPageAtType:
    def test_type_mayuscula_estandar(self):
        html = """<script type="application/ld+json">
        {"@type": "Product", "name": "X", "offers": {"price": "10.00", "availability": "InStock"}}
        </script>"""
        item = GenericJsonLdScraper._parse_product_page(html)
        assert item["name"] == "X"
        assert item["stock_status"] == "DISPONIBLE"

    def test_type_minuscula_caso_isekai(self):
        html = """<script type="application/ld+json">
        {"@type": "product", "name": "Y", "offers": {"price": "10.00", "availability": "InStock"}}
        </script>"""
        item = GenericJsonLdScraper._parse_product_page(html)
        assert item is not None
        assert item["name"] == "Y"


class TestParseProductPageOfertaAnidada:
    def test_oferta_directa(self):
        html = """<script type="application/ld+json">
        {"@type": "Product", "name": "X", "offers": {"price": "15.50", "availability": "InStock"}}
        </script>"""
        item = GenericJsonLdScraper._parse_product_page(html)
        assert item["price"] == 15.50

    def test_oferta_anidada_en_aggregate_offer(self):
        # Caso ISEKAI: offers.offers.price/availability en vez de offers.price directo.
        html = """<script type="application/ld+json">
        {"@type": "product", "name": "Y", "offers": {"offers": {"price": "22.00", "availability": "InStock"}}}
        </script>"""
        item = GenericJsonLdScraper._parse_product_page(html)
        assert item["price"] == 22.00
        assert item["stock_status"] == "DISPONIBLE"


class TestParseProductPageDisponibilidadFallback:
    def test_sin_availability_en_jsonld_cae_a_span(self):
        # Caso Comic Stores/Freak Point: JSON-LD sin availability, se lee
        # de un <span class="availability"> aparte.
        html = """
        <script type="application/ld+json">
        {"@type": "Product", "name": "Z", "offers": {"price": "10.00"}}
        </script>
        <span class="availability">OutOfStock</span>
        """
        item = GenericJsonLdScraper._parse_product_page(html)
        assert item["stock_status"] == "AGOTADO"

    def test_sin_availability_ni_span_cae_a_meta(self):
        html = """
        <script type="application/ld+json">
        {"@type": "Product", "name": "Z", "offers": {"price": "10.00"}}
        </script>
        <meta property="product:availability" content="out of stock">
        """
        item = GenericJsonLdScraper._parse_product_page(html)
        assert item["stock_status"] == "AGOTADO"

    def test_ninguna_fuente_de_disponibilidad_es_desconocido_no_disponible(self):
        html = """<script type="application/ld+json">
        {"@type": "Product", "name": "Z", "offers": {"price": "10.00"}}
        </script>"""
        item = GenericJsonLdScraper._parse_product_page(html)
        assert item["stock_status"] == "DESCONOCIDO"

    def test_sin_jsonld_en_absoluto_devuelve_none(self):
        assert GenericJsonLdScraper._parse_product_page("<html>nada aquí</html>") is None


class TestCollectProductUrls:
    def test_paginacion_por_rel_next(self, requests_mock):
        scraper = make_scraper()
        requests_mock.get(
            "https://tienda.example/categoria",
            text='<a class="producto" href="/p1">p1</a><a rel="next" href="/categoria?page=2">2</a>',
        )
        requests_mock.get(
            "https://tienda.example/categoria?page=2",
            text='<a class="producto" href="/p2">p2</a>',
        )
        import requests as requests_lib
        urls = scraper._collect_product_urls(requests_lib.Session())
        assert urls == ["https://tienda.example/p1", "https://tienda.example/p2"]

    def test_paginacion_por_enlaces_numerados(self, requests_mock):
        scraper = make_scraper()
        requests_mock.get(
            "https://tienda.example/categoria",
            text='<a class="producto" href="/p1">p1</a><a class="page-numbers" href="/categoria/2">2</a>',
        )
        requests_mock.get(
            "https://tienda.example/categoria/2",
            text='<a class="producto" href="/p2">p2</a>',
        )
        import requests as requests_lib
        urls = scraper._collect_product_urls(requests_lib.Session())
        assert set(urls) == {"https://tienda.example/p1", "https://tienda.example/p2"}

    def test_pagina_sin_enlaces_de_producto_nuevos_no_rompe(self, requests_mock):
        scraper = make_scraper()
        requests_mock.get("https://tienda.example/categoria", text="<html>sin productos</html>")
        import requests as requests_lib
        urls = scraper._collect_product_urls(requests_lib.Session())
        assert urls == []


class TestGenericJsonLdRefreshProduct:
    def _cfg(self):
        return StoreConfig("Tienda", "https://tienda.example", Platform.GENERIC_JSONLD,
                            jsonld_listing_urls=("https://tienda.example/categoria",),
                            jsonld_product_link_selector="a.producto")

    def test_reutiliza_el_mismo_parser(self, requests_mock):
        requests_mock.get(
            "https://tienda.example/p1",
            text='<script type="application/ld+json">{"@type":"Product","name":"X","offers":{"price":"12.50","availability":"InStock"}}</script>',
        )
        outcome = GenericJsonLdScraper.refresh_product(self._cfg(), "https://tienda.example/p1")
        assert outcome.status == "modified"
        assert outcome.variants[0].price == 12.50
        assert outcome.variants[0].name == "X"

    def test_304_not_modified(self, requests_mock):
        requests_mock.get("https://tienda.example/p1", status_code=304)
        outcome = GenericJsonLdScraper.refresh_product(self._cfg(), "https://tienda.example/p1")
        assert outcome.status == "not_modified"

    def test_sin_jsonld_es_error(self, requests_mock):
        requests_mock.get("https://tienda.example/p1", text="<html>tema cambiado</html>")
        outcome = GenericJsonLdScraper.refresh_product(self._cfg(), "https://tienda.example/p1")
        assert outcome.status == "error"


class TestGenericJsonLdScrape:
    def test_scrape_completo_dos_productos(self, requests_mock):
        scraper = make_scraper()
        requests_mock.get(
            "https://tienda.example/categoria",
            text='<a class="producto" href="/p1">p1</a><a class="producto" href="/p2">p2</a>',
        )
        requests_mock.get(
            "https://tienda.example/p1",
            text='<script type="application/ld+json">{"@type":"Product","name":"P1","offers":{"price":"10.00","availability":"InStock"}}</script>',
        )
        requests_mock.get(
            "https://tienda.example/p2",
            text='<script type="application/ld+json">{"@type":"Product","name":"P2","offers":{"price":"20.00","availability":"OutOfStock"}}</script>',
        )
        products = scraper.scrape()
        assert len(products) == 2
        by_name = {p.name: p.stock_status for p in products}
        assert by_name["P1"] == "DISPONIBLE"
        assert by_name["P2"] == "AGOTADO"

    def test_producto_sin_jsonld_se_omite_sin_abortar(self, requests_mock):
        scraper = make_scraper()
        requests_mock.get(
            "https://tienda.example/categoria",
            text='<a class="producto" href="/p1">p1</a><a class="producto" href="/p2">p2</a>',
        )
        requests_mock.get("https://tienda.example/p1", text="<html>tema cambiado</html>")
        requests_mock.get(
            "https://tienda.example/p2",
            text='<script type="application/ld+json">{"@type":"Product","name":"P2","offers":{"price":"20.00","availability":"InStock"}}</script>',
        )
        products = scraper.scrape()
        assert len(products) == 1
        assert products[0].name == "P2"

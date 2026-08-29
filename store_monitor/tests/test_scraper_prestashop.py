"""Tests de PrestaShopScraper -- sección 2.3 del plan de pruebas."""

from __future__ import annotations

import pytest

from shared.domain import Platform, StoreConfig
from http_client import StoreLogger
from scrapers.prestashop import PrestaShopScraper


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import scrapers.prestashop as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)  # incluye el time.sleep(1.5) fijo tras la home


def make_scraper():
    cfg = StoreConfig("Tienda", "https://tienda.example", Platform.PRESTASHOP,
                       prestashop_category_url="https://tienda.example/10-categoria")
    logger = StoreLogger("Tienda", {})
    return PrestaShopScraper(cfg, logger, delay=0)


def category_page(items_html, max_page=1):
    pagination = "".join(
        f'<a class="js-search-link" href="#">{p}</a>' for p in range(1, max_page + 1)
    )
    return f"""
    <html><body>
        {items_html}
        <nav class="pagination">{pagination}</nav>
    </body></html>
    """


def product_article(id_product="1", name="Booster OP16", available=True, price_content="19.95"):
    disponible_html = '<span class="product-available">En stock</span>' if available else ""
    return f"""
    <article class="product-miniature" data-id-product="{id_product}">
        <h3 class="product-title"><a href="https://tienda.example/producto-{id_product}">{name}</a></h3>
        <span class="product-price" content="{price_content}">{price_content}€</span>
        <span class="product-reference">REF{id_product}</span>
        {disponible_html}
    </article>
    """


class TestPrestaShopScraper:
    def _mock_home(self, requests_mock):
        requests_mock.get("https://tienda.example/", text="<html>home</html>")

    def test_pagina_con_productos_nuevos_se_acumula_y_sigue_paginando(self, requests_mock):
        self._mock_home(requests_mock)
        requests_mock.get(
            "https://tienda.example/10-categoria",
            [
                {"text": category_page(product_article("1") + product_article("2"), max_page=2)},
                {"text": category_page(product_article("3"), max_page=2)},
            ],
        )
        scraper = make_scraper()
        products = scraper.scrape()
        assert len(products) == 3
        assert requests_mock.call_count == 3  # home + página 1 + página 2

    def test_pagina_que_repite_productos_ya_vistos_corta_paginacion(self, requests_mock):
        self._mock_home(requests_mock)
        # max_page=5 miente sobre cuántas páginas hay de verdad -- la
        # tienda re-sirve la página 1 para siempre a partir de la 2.
        requests_mock.get(
            "https://tienda.example/10-categoria",
            text=category_page(product_article("1") + product_article("2"), max_page=5),
        )
        scraper = make_scraper()
        products = scraper.scrape()
        assert len(products) == 2  # solo los de la primera página real
        assert requests_mock.call_count == 3  # home + página 1 + página 2 (donde detecta el estancamiento)

    def test_limite_duro_de_50_paginas(self, requests_mock):
        self._mock_home(requests_mock)
        # Cada página aporta un id_product NUEVO y distinto -- nunca se
        # corta por "repetidos", solo por el límite duro de MAX_LISTING_PAGES.
        responses = [
            {"text": category_page(product_article(str(i)), max_page=999)}
            for i in range(1, 60)
        ]
        requests_mock.get("https://tienda.example/10-categoria", responses)
        scraper = make_scraper()
        products = scraper.scrape()
        assert len(products) == 50  # MAX_LISTING_PAGES, no las 59 "disponibles"

    def test_home_irrecuperable_aborta_sin_excepcion(self, requests_mock):
        import requests
        requests_mock.get("https://tienda.example/", exc=requests.exceptions.ConnectTimeout)
        scraper = make_scraper()
        products = scraper.scrape()
        assert products == []


class TestNombreTruncado:
    """Tema IQIT recorta el título en el listado (visto real en Distrito
    Zero) -- mismo patrón que WooCommerceScraper (Arte9/Madara), el
    disparador es el "..." literal en el nombre, no la tienda."""

    def _mock_home(self, requests_mock):
        requests_mock.get("https://tienda.example/", text="<html>home</html>")

    def test_nombre_truncado_se_resuelve_desde_la_ficha(self, requests_mock):
        self._mock_home(requests_mock)
        requests_mock.get(
            "https://tienda.example/10-categoria",
            text=category_page(product_article("1", name="ONE PIECE CARD GAME ULTRA DECK -THE THREE...")),
        )
        requests_mock.get(
            "https://tienda.example/producto-1",
            text='<h1>ONE PIECE CARD GAME ULTRA DECK -THE THREE CAPTAINS- ST10</h1>',
        )
        scraper = make_scraper()
        products = scraper.scrape()
        assert len(products) == 1
        assert products[0].name == "ONE PIECE CARD GAME ULTRA DECK -THE THREE CAPTAINS- ST10"

    def test_nombre_no_truncado_no_visita_la_ficha(self, requests_mock):
        self._mock_home(requests_mock)
        requests_mock.get(
            "https://tienda.example/10-categoria",
            text=category_page(product_article("1", name="Booster Box OP-16 EN")),
        )
        # A propósito SIN registrar https://tienda.example/producto-1 -- si
        # el scraper la visitara, requests_mock lanzaría NoMockAddress.
        scraper = make_scraper()
        products = scraper.scrape()
        assert products[0].name == "Booster Box OP-16 EN"

    def test_si_la_ficha_falla_se_queda_con_el_nombre_truncado_original(self, requests_mock):
        self._mock_home(requests_mock)
        requests_mock.get(
            "https://tienda.example/10-categoria",
            text=category_page(product_article("1", name="RED STARTER DECK ONE PIECE . . .")),
        )
        requests_mock.get("https://tienda.example/producto-1", status_code=500)
        scraper = make_scraper()
        products = scraper.scrape()
        # No revienta el barrido ni descarta el producto -- se queda con lo
        # que ya tenía del listado.
        assert products[0].name == "RED STARTER DECK ONE PIECE . . ."


class TestPrestaShopRefreshProduct:
    def test_microdata_price_availability_instock(self, requests_mock):
        html = """
        <html><body>
            <h1>ONE PIECE CARD GAME - STARTER DECK ST36 - EN</h1>
            <span itemprop="price" content="19.95">19,95€</span>
            <link itemprop="availability" href="https://schema.org/InStock">
        </body></html>
        """
        requests_mock.get("https://tienda.example/producto-1", text=html)
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.PRESTASHOP,
                           prestashop_category_url="https://tienda.example/10-categoria")
        outcome = PrestaShopScraper.refresh_product(cfg, "https://tienda.example/producto-1")
        assert outcome.status == "modified"
        assert outcome.variants[0].price == 19.95
        assert outcome.variants[0].stock_status == "DISPONIBLE"
        assert outcome.variants[0].name == "ONE PIECE CARD GAME - STARTER DECK ST36 - EN"

    def test_microdata_availability_outofstock(self, requests_mock):
        html = """
        <html><body>
            <h1>Producto agotado</h1>
            <span itemprop="price" content="10.00">10,00€</span>
            <link itemprop="availability" href="https://schema.org/OutOfStock">
        </body></html>
        """
        requests_mock.get("https://tienda.example/producto-2", text=html)
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.PRESTASHOP,
                           prestashop_category_url="https://tienda.example/10-categoria")
        outcome = PrestaShopScraper.refresh_product(cfg, "https://tienda.example/producto-2")
        assert outcome.variants[0].stock_status == "AGOTADO"

    def test_sin_microdata_de_precio_devuelve_error(self, requests_mock):
        requests_mock.get("https://tienda.example/producto-3", text="<html>tema cambiado</html>")
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.PRESTASHOP,
                           prestashop_category_url="https://tienda.example/10-categoria")
        outcome = PrestaShopScraper.refresh_product(cfg, "https://tienda.example/producto-3")
        assert outcome.status == "error"

    def test_304_not_modified(self, requests_mock):
        requests_mock.get("https://tienda.example/producto-4", status_code=304)
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.PRESTASHOP,
                           prestashop_category_url="https://tienda.example/10-categoria")
        outcome = PrestaShopScraper.refresh_product(cfg, "https://tienda.example/producto-4")
        assert outcome.status == "not_modified"

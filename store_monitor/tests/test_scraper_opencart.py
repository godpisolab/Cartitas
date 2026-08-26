"""Tests de OpenCartScraper -- sección 2.5 del plan de pruebas."""

from __future__ import annotations

import pytest

from base_script import Platform, StoreConfig, StoreLogger
from scrapers.opencart import OpenCartScraper


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import scrapers.opencart as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


def make_scraper():
    cfg = StoreConfig("Tienda", "https://tienda.example", Platform.OPENCART,
                       opencart_category_url="https://tienda.example/index.php?route=product/category&path=77")
    logger = StoreLogger("Tienda", {})
    return OpenCartScraper(cfg, logger, delay=0)


def product_card(name="Booster OP16", url="index.php?route=product/product&product_id=123",
                  price="19.95€", extra_text=""):
    return f"""
    <div class="product-thumb">
        <div class="image"><a href="{url}"><img src="https://tienda.example/img.jpg"></a></div>
        <div class="caption">
            <h4><a href="{url}">{name}</a></h4>
            <p class="price">{price}</p>
            <p>{extra_text}</p>
        </div>
    </div>
    """


def category_page(cards_html, has_next=False):
    next_link = '<ul class="pagination"><a rel="next" href="?page=2">2</a></ul>' if has_next else ""
    return f"<html><body>{cards_html}{next_link}</body></html>"


class TestParsePage:
    def test_texto_agotado_en_la_tarjeta_es_agotado(self):
        html = category_page(product_card(extra_text="Agotado"))
        items, _ = OpenCartScraper._parse_page(html)
        assert items[0]["stock_status"] == "AGOTADO"

    def test_texto_en_stock_es_disponible(self):
        html = category_page(product_card(extra_text="En stock"))
        items, _ = OpenCartScraper._parse_page(html)
        assert items[0]["stock_status"] == "DISPONIBLE"

    def test_sin_ninguna_senal_de_stock_es_desconocido(self):
        html = category_page(product_card(extra_text=""))
        items, _ = OpenCartScraper._parse_page(html)
        assert items[0]["stock_status"] == "DESCONOCIDO"

    def test_id_product_se_extrae_de_la_url(self):
        html = category_page(product_card(url="index.php?route=product/product&product_id=456"))
        items, _ = OpenCartScraper._parse_page(html)
        assert items[0]["id_product"] == "456"

    def test_url_sin_product_id_no_revienta(self):
        html = category_page(product_card(url="index.php?route=product/product"))
        items, _ = OpenCartScraper._parse_page(html)
        assert items[0]["id_product"] is None

    def test_has_next_true_con_enlace_rel_next(self):
        html = category_page(product_card(), has_next=True)
        _items, has_next = OpenCartScraper._parse_page(html)
        assert has_next is True

    def test_has_next_false_sin_enlace(self):
        html = category_page(product_card(), has_next=False)
        _items, has_next = OpenCartScraper._parse_page(html)
        assert has_next is False


class TestOpenCartScrape:
    def test_pagina_sin_productos_no_avanza(self, requests_mock):
        requests_mock.get(
            "https://tienda.example/index.php?route=product/category&path=77",
            text=category_page(""),
        )
        products = make_scraper().scrape()
        assert products == []

    def test_dos_paginas_se_acumulan(self, requests_mock):
        import re
        requests_mock.get(
            re.compile(r"https://tienda\.example/index\.php\?route=product/category.*"),
            [
                {"text": category_page(product_card(url="index.php?route=product/product&product_id=1"), has_next=True)},
                {"text": category_page(product_card(url="index.php?route=product/product&product_id=2"), has_next=False)},
            ],
        )
        products = make_scraper().scrape()
        assert len(products) == 2


class TestOpenCartRefreshProduct:
    def test_precio_con_descuento_concatenado_coge_el_ultimo(self, requests_mock):
        html = """
        <html><body>
            <h1>Clic para ampliar One Piece Starter Deck</h1>
            <p class="price">120,00€ 99,00€</p>
        </body></html>
        """
        requests_mock.get("https://tienda.example/producto-1", text=html)
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.OPENCART,
                           opencart_category_url="https://tienda.example/index.php?route=product/category&path=77")
        outcome = OpenCartScraper.refresh_product(cfg, "https://tienda.example/producto-1")
        assert outcome.variants[0].price == 99.00

    def test_prefijo_clic_para_ampliar_se_elimina_del_nombre(self, requests_mock):
        html = """
        <html><body>
            <h1>Clic para ampliar One Piece ST31-36 Starter Deck</h1>
            <p class="price">120,00€</p>
        </body></html>
        """
        requests_mock.get("https://tienda.example/producto-2", text=html)
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.OPENCART,
                           opencart_category_url="https://tienda.example/index.php?route=product/category&path=77")
        outcome = OpenCartScraper.refresh_product(cfg, "https://tienda.example/producto-2")
        assert outcome.variants[0].name == "One Piece ST31-36 Starter Deck"

    def test_stock_siempre_desconocido_en_el_refresco_individual(self, requests_mock):
        # A propósito: ver el hallazgo real de "opciones disponibles" (falso
        # positivo) documentado en _parse_product_detail -- por eso el
        # refresco individual nunca intenta adivinar el stock.
        html = """
        <html><body>
            <h1>Producto</h1>
            <p class="price">10,00€</p>
            <p>Opciones disponibles: Talla</p>
        </body></html>
        """
        requests_mock.get("https://tienda.example/producto-3", text=html)
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.OPENCART,
                           opencart_category_url="https://tienda.example/index.php?route=product/category&path=77")
        outcome = OpenCartScraper.refresh_product(cfg, "https://tienda.example/producto-3")
        assert outcome.variants[0].stock_status == "DESCONOCIDO"

    def test_sin_price_tag_devuelve_error(self, requests_mock):
        requests_mock.get("https://tienda.example/producto-4", text="<html><body>sin precio</body></html>")
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.OPENCART,
                           opencart_category_url="https://tienda.example/index.php?route=product/category&path=77")
        outcome = OpenCartScraper.refresh_product(cfg, "https://tienda.example/producto-4")
        assert outcome.status == "error"

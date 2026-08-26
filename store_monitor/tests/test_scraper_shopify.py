"""Tests de ShopifyScraper -- sección 2.1 del plan de pruebas. HTTP
mockeado con requests-mock, sin red real."""

from __future__ import annotations

import pytest

from domain import Platform, StoreConfig
from http_client import StoreLogger
from scrapers.shopify import ShopifyScraper


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import scrapers.shopify as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


def make_scraper(collection="one-piece"):
    cfg = StoreConfig("Tienda", "https://tienda.example", Platform.SHOPIFY, shopify_collection=collection)
    logger = StoreLogger("Tienda", {})
    return ShopifyScraper(cfg, logger, delay=0)


def products_json(products):
    return {"products": products}


def variant(title="Default Title", price="10.00", available=True, sku="SKU1"):
    return {"title": title, "price": price, "available": available, "sku": sku}


def product_item(title="Booster OP16", handle="booster-op16", variants=None, images=None):
    return {
        "id": 1,
        "title": title,
        "handle": handle,
        "images": images if images is not None else [{"src": "https://tienda.example/img.jpg"}],
        "variants": variants if variants is not None else [variant()],
    }


class TestShopifyScraper:
    def test_un_producto_una_variante_disponible(self, requests_mock):
        scraper = make_scraper()
        requests_mock.get(
            "https://tienda.example/collections/one-piece/products.json",
            [{"json": products_json([product_item()])}, {"json": products_json([])}],
        )
        products = scraper.scrape()
        assert len(products) == 1
        assert products[0].stock_status == "DISPONIBLE"
        assert products[0].url == "https://tienda.example/products/booster-op16"

    def test_dos_variantes_stock_mixto_genera_dos_products_y_aviso(self, requests_mock):
        scraper = make_scraper()
        item = product_item(variants=[
            variant(title="Inglés", available=True),
            variant(title="Japonés", available=False),
        ])
        requests_mock.get(
            "https://tienda.example/collections/one-piece/products.json",
            [{"json": products_json([item])}, {"json": products_json([])}],
        )
        products = scraper.scrape()
        assert len(products) == 2
        statuses = {p.variant: p.stock_status for p in products}
        assert statuses["Inglés"] == "DISPONIBLE"
        assert statuses["Japonés"] == "AGOTADO"
        assert "stock mixto" in (scraper.logger.last_error or "")

    def test_products_vacio_detiene_sin_pedir_siguiente_pagina(self, requests_mock):
        scraper = make_scraper()
        m = requests_mock.get(
            "https://tienda.example/collections/one-piece/products.json",
            json=products_json([]),
        )
        products = scraper.scrape()
        assert products == []
        assert m.call_count == 1

    def test_pagina_1_con_productos_pagina_2_vacia_pagina_correctamente(self, requests_mock):
        scraper = make_scraper()
        requests_mock.get(
            "https://tienda.example/collections/one-piece/products.json",
            [{"json": products_json([product_item(), product_item(handle="otro")])},
             {"json": products_json([])}],
        )
        products = scraper.scrape()
        assert len(products) == 2
        assert requests_mock.call_count == 2

    def test_respuesta_no_200_en_pagina_2_conserva_productos_de_pagina_1(self, requests_mock):
        scraper = make_scraper()
        requests_mock.get(
            "https://tienda.example/collections/one-piece/products.json",
            [{"json": products_json([product_item()])}, {"status_code": 500}],
        )
        products = scraper.scrape()
        assert len(products) == 1  # de la página 1, no se descarta por el fallo de la página 2

    def test_respuesta_no_json_aborta_sin_excepcion(self, requests_mock):
        scraper = make_scraper()
        requests_mock.get(
            "https://tienda.example/collections/one-piece/products.json",
            text="<html>esto no es json</html>",
        )
        products = scraper.scrape()
        assert products == []


class TestShopifyRefreshProduct:
    def _cfg(self):
        return StoreConfig("Tienda", "https://tienda.example", Platform.SHOPIFY, shopify_collection="x")

    def test_una_variante_modified(self, requests_mock):
        requests_mock.get(
            "https://tienda.example/products/booster-op16.json",
            json={"product": {"title": "Booster OP16", "variants": [
                {"title": "Default Title", "price": "99.95", "available": True},
            ]}},
        )
        outcome = ShopifyScraper.refresh_product(self._cfg(), "https://tienda.example/products/booster-op16")
        assert outcome.status == "modified"
        assert outcome.variants[0].price == 99.95
        assert outcome.variants[0].name == "Booster OP16"

    def test_varias_variantes_devuelve_una_por_variante(self, requests_mock):
        requests_mock.get(
            "https://tienda.example/products/booster-op16.json",
            json={"product": {"title": "Booster OP16", "variants": [
                {"title": "Inglés", "price": "10.00", "available": True},
                {"title": "Japonés", "price": "8.00", "available": False},
            ]}},
        )
        outcome = ShopifyScraper.refresh_product(self._cfg(), "https://tienda.example/products/booster-op16")
        assert len(outcome.variants) == 2
        by_variant = {v.variant: v.stock_status for v in outcome.variants}
        assert by_variant == {"Inglés": "DISPONIBLE", "Japonés": "AGOTADO"}

    def test_304_not_modified(self, requests_mock):
        requests_mock.get("https://tienda.example/products/booster-op16.json", status_code=304)
        outcome = ShopifyScraper.refresh_product(self._cfg(), "https://tienda.example/products/booster-op16")
        assert outcome.status == "not_modified"

    def test_fallo_de_red_es_error(self, requests_mock):
        import requests
        requests_mock.get("https://tienda.example/products/booster-op16.json",
                           exc=requests.exceptions.ConnectTimeout)
        outcome = ShopifyScraper.refresh_product(self._cfg(), "https://tienda.example/products/booster-op16")
        assert outcome.status == "error"

    def test_sin_variantes_en_la_respuesta_es_error(self, requests_mock):
        requests_mock.get("https://tienda.example/products/booster-op16.json",
                           json={"product": {"title": "X", "variants": []}})
        outcome = ShopifyScraper.refresh_product(self._cfg(), "https://tienda.example/products/booster-op16")
        assert outcome.status == "error"

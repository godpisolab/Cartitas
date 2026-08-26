"""_robots_check_target() para las 6 plataformas -- no estaba cubierto
(solo se probaba Shopify indirectamente en test_dispatcher.py). Pura
función de mapeo, barata de cubrir del todo y con riesgo real si se rompe
(afecta directamente qué URL se comprueba contra robots.txt, A.2)."""

from __future__ import annotations

import dispatcher
from domain import Platform, StoreConfig


class TestRobotsCheckTarget:
    def test_shopify(self):
        cfg = StoreConfig("T", "https://t.example", Platform.SHOPIFY, shopify_collection="one-piece")
        assert dispatcher._robots_check_target(cfg) == "https://t.example/collections/one-piece"

    def test_prestashop(self):
        cfg = StoreConfig("T", "https://t.example", Platform.PRESTASHOP,
                           prestashop_category_url="https://t.example/10-categoria")
        assert dispatcher._robots_check_target(cfg) == "https://t.example/10-categoria"

    def test_woocommerce_con_fallback_paths(self):
        cfg = StoreConfig("T", "https://t.example", Platform.WOOCOMMERCE,
                           woocommerce_category_slug="op",
                           woocommerce_fallback_paths=("product-category/op",))
        assert dispatcher._robots_check_target(cfg) == "https://t.example/product-category/op/"

    def test_woocommerce_sin_fallback_paths_devuelve_none(self):
        # Solo Store API, sin ruta HTML que comprobar contra robots.txt.
        cfg = StoreConfig("T", "https://t.example", Platform.WOOCOMMERCE, woocommerce_category_slug="op")
        assert dispatcher._robots_check_target(cfg) is None

    def test_odoo(self):
        cfg = StoreConfig("T", "https://t.example", Platform.ODOO,
                           odoo_category_url="https://t.example/shop/category/op-10")
        assert dispatcher._robots_check_target(cfg) == "https://t.example/shop/category/op-10"

    def test_opencart(self):
        cfg = StoreConfig("T", "https://t.example", Platform.OPENCART,
                           opencart_category_url="https://t.example/index.php?route=product/category&path=1")
        assert dispatcher._robots_check_target(cfg) == "https://t.example/index.php?route=product/category&path=1"

    def test_generic_jsonld(self):
        cfg = StoreConfig("T", "https://t.example", Platform.GENERIC_JSONLD,
                           jsonld_listing_urls=("https://t.example/categoria",),
                           jsonld_product_link_selector="a.producto")
        assert dispatcher._robots_check_target(cfg) == "https://t.example/categoria"

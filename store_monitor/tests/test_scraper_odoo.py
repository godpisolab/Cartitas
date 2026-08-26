"""Tests de OdooScraper -- sección 2.4 del plan de pruebas."""

from __future__ import annotations

import pytest

from base_script import Platform, StoreConfig, StoreLogger
from scrapers.odoo import OdooScraper


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import scrapers.odoo as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


def make_scraper():
    cfg = StoreConfig("Tienda", "https://tienda.example", Platform.ODOO,
                       odoo_category_url="https://tienda.example/shop/category/one-piece-10")
    logger = StoreLogger("Tienda", {})
    return OdooScraper(cfg, logger, delay=0)


def grid_page(product_urls, has_next=False):
    cards = "".join(
        f'<div class="oe_product_cart"><a itemprop="url" href="{u}">ver</a></div>' for u in product_urls
    )
    next_link = '<nav class="o_website_sale_pager"><a rel="next" href="#">Siguiente</a></nav>' if has_next else ""
    return f"<html><body>{cards}{next_link}</body></html>"


def product_page_jsonld(availability="https://schema.org/InStock", price="15.00", name="Booster OP16"):
    return f"""
    <html><body>
    <script type="application/ld+json">
    {{"@type": "Product", "name": "{name}", "offers": {{"price": "{price}", "availability": "{availability}"}}}}
    </script>
    </body></html>
    """


class TestOdooScraper:
    def test_visita_cada_url_de_producto_individualmente(self, requests_mock):
        requests_mock.get(
            "https://tienda.example/shop/category/one-piece-10",
            text=grid_page(["https://tienda.example/shop/p1", "https://tienda.example/shop/p2"]),
        )
        requests_mock.get("https://tienda.example/shop/p1", text=product_page_jsonld(name="Producto 1"))
        requests_mock.get("https://tienda.example/shop/p2", text=product_page_jsonld(name="Producto 2"))

        products = make_scraper().scrape()

        assert len(products) == 2
        assert {p.name for p in products} == {"Producto 1", "Producto 2"}

    def test_instock_es_disponible(self, requests_mock):
        requests_mock.get(
            "https://tienda.example/shop/category/one-piece-10",
            text=grid_page(["https://tienda.example/shop/p1"]),
        )
        requests_mock.get("https://tienda.example/shop/p1",
                           text=product_page_jsonld(availability="https://schema.org/InStock"))
        products = make_scraper().scrape()
        assert products[0].stock_status == "DISPONIBLE"

    def test_outofstock_es_agotado(self, requests_mock):
        requests_mock.get(
            "https://tienda.example/shop/category/one-piece-10",
            text=grid_page(["https://tienda.example/shop/p1"]),
        )
        requests_mock.get("https://tienda.example/shop/p1",
                           text=product_page_jsonld(availability="https://schema.org/OutOfStock"))
        products = make_scraper().scrape()
        assert products[0].stock_status == "AGOTADO"

    def test_sin_jsonld_en_absoluto_se_omite_el_producto_con_aviso(self, requests_mock):
        # Página de producto SIN ningún bloque JSON-LD de tipo Product --
        # _parse_product_jsonld devuelve None y el producto se omite entero
        # (no se inventa una fila con stock desconocido).
        requests_mock.get(
            "https://tienda.example/shop/category/one-piece-10",
            text=grid_page(["https://tienda.example/shop/p1"]),
        )
        requests_mock.get("https://tienda.example/shop/p1", text="<html><body>sin json-ld</body></html>")
        products = make_scraper().scrape()
        assert products == []

    def test_hallazgo_jsonld_sin_availability_cae_a_disponible_no_desconocido(self, requests_mock):
        """HALLAZGO al escribir este test: si el bloque JSON-LD de tipo
        Product SÍ existe pero su `offers` no trae `availability` en
        absoluto, _parse_product_jsonld devuelve stock_status="DISPONIBLE"
        por defecto -- NO "DESCONOCIDO".

        Esto es inconsistente con el resto del proyecto: OpenCartScraper
        documenta explícitamente "decisión deliberada de NO adivinar...
        DESCONOCIDO en vez de DISPONIBLE", y GenericJsonLdScraper hace
        justo eso (cae a DESCONOCIDO si no encuentra disponibilidad en
        ningún sitio). OdooScraper es la excepción: `"outofstock" in "" or
        "discontinued" in ""` es False, así que el else por defecto es
        DISPONIBLE. En la práctica no se ha visto ninguna tienda Odoo real
        (TCG Legacy) sin `offers.availability`, pero si apareciera una,
        este scraper la marcaría DISPONIBLE en vez de DESCONOCIDO -- lo
        contrario de la filosofía del resto del proyecto. Documentado aquí
        en vez de "arreglado" sin que se pida explícitamente."""
        requests_mock.get(
            "https://tienda.example/shop/category/one-piece-10",
            text=grid_page(["https://tienda.example/shop/p1"]),
        )
        html = """
        <html><body>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Sin disponibilidad", "offers": {"price": "10.00"}}
        </script>
        </body></html>
        """
        requests_mock.get("https://tienda.example/shop/p1", text=html)
        products = make_scraper().scrape()
        assert products[0].stock_status == "DISPONIBLE"  # comportamiento real actual, no el ideal

    def test_pagina_sin_urls_nuevas_corta_paginacion(self, requests_mock):
        requests_mock.get(
            "https://tienda.example/shop/category/one-piece-10",
            text=grid_page(["https://tienda.example/shop/p1"], has_next=True),
        )
        requests_mock.get(
            "https://tienda.example/shop/category/one-piece-10/page/2",
            text=grid_page(["https://tienda.example/shop/p1"], has_next=True),  # misma URL, "repite"
        )
        requests_mock.get("https://tienda.example/shop/p1", text=product_page_jsonld())

        products = make_scraper().scrape()

        assert len(products) == 1
        assert requests_mock.call_count == 3  # listado pág1 + listado pág2 (detecta estancamiento) + 1 producto

    def test_limite_duro_de_50_paginas(self, requests_mock):
        import re
        requests_mock.get(
            re.compile(r"https://tienda\.example/shop/category/one-piece-10.*"),
            [{"text": grid_page([f"https://tienda.example/shop/p{i}"], has_next=True)} for i in range(1, 60)],
        )
        requests_mock.get(re.compile(r"https://tienda\.example/shop/p\d+"), text=product_page_jsonld())

        products = make_scraper().scrape()

        assert len(products) == 50  # MAX_LISTING_PAGES


class TestOdooRefreshProduct:
    def test_reutiliza_el_mismo_parser_jsonld(self, requests_mock):
        requests_mock.get("https://tienda.example/shop/p1", text=product_page_jsonld(price="25.50"))
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.ODOO,
                           odoo_category_url="https://tienda.example/shop/category/one-piece-10")
        outcome = OdooScraper.refresh_product(cfg, "https://tienda.example/shop/p1")
        assert outcome.status == "modified"
        assert outcome.variants[0].price == 25.50

    def test_304_not_modified(self, requests_mock):
        requests_mock.get("https://tienda.example/shop/p1", status_code=304)
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.ODOO,
                           odoo_category_url="https://tienda.example/shop/category/one-piece-10")
        outcome = OdooScraper.refresh_product(cfg, "https://tienda.example/shop/p1")
        assert outcome.status == "not_modified"

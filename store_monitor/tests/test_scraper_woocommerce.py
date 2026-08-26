"""Tests de WooCommerceScraper -- sección 2.2 del plan de pruebas. La
plataforma con más lógica (scoping AND, fallback API->HTML, paginación,
parseo HTML defensivo) -- la que más cobertura merece."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shared.domain import Platform, StoreConfig
from http_client import StoreLogger
from scrapers.woocommerce import WooCommerceScraper


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import scrapers.woocommerce as mod
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


def make_scraper(**config_kwargs):
    cfg = StoreConfig("Tienda", "https://tienda.example", Platform.WOOCOMMERCE,
                       woocommerce_fallback_paths=("categoria-producto/one-piece",), **config_kwargs)
    logger = StoreLogger("Tienda", {})
    return WooCommerceScraper(cfg, logger, delay=0)


def api_item(name="Booster OP16", id_=1, category_slug="one-piece", price="1000", in_stock=True):
    return {
        "id": id_, "name": name, "permalink": f"https://tienda.example/producto/{id_}",
        "sku": f"SKU{id_}",
        "categories": [{"slug": category_slug, "name": category_slug}],
        "prices": {"price": price, "currency_minor_unit": 2},
        "is_in_stock": in_stock,
        "images": [{"src": "https://tienda.example/img.jpg"}],
    }


# ===========================================================================
# _product_in_scope -- la corrección del bug histórico de Arte9/ZIAL
# ===========================================================================

class TestProductInScope:
    def test_solo_category_slug_item_con_esa_categoria(self):
        item = {"categories": [{"slug": "one-piece"}], "name": "x"}
        assert WooCommerceScraper._product_in_scope(item, "one-piece", ()) is True

    def test_solo_category_slug_item_con_otra_categoria(self):
        item = {"categories": [{"slug": "pokemon"}], "name": "x"}
        assert WooCommerceScraper._product_in_scope(item, "one-piece", ()) is False

    def test_solo_name_must_include_case_insensitive(self):
        item = {"categories": [], "name": "One Piece TCG Booster"}
        assert WooCommerceScraper._product_in_scope(item, None, ("one piece",)) is True

    def test_ambos_configurados_es_interseccion_no_union(self):
        # Cumple category_slug pero NO name_must_include -- debe ser False
        # (AND, no OR). Este es el test que protege la corrección explícita
        # documentada en el código.
        item = {"categories": [{"slug": "jc-tcg"}], "name": "Magic: The Gathering Booster"}
        assert WooCommerceScraper._product_in_scope(item, "jc-tcg", ("one piece",)) is False

    def test_ambos_configurados_cumple_los_dos_es_true(self):
        item = {"categories": [{"slug": "jc-tcg"}], "name": "One Piece TCG Booster"}
        assert WooCommerceScraper._product_in_scope(item, "jc-tcg", ("one piece",)) is True

    def test_dos_palabras_en_name_must_include_falta_una_es_false(self):
        item = {"categories": [], "name": "One Piece Starter Deck"}
        assert WooCommerceScraper._product_in_scope(item, None, ("one piece", "booster")) is False

    def test_sin_scoping_configurado_siempre_true(self):
        item = {"categories": [], "name": "cualquier cosa"}
        assert WooCommerceScraper._product_in_scope(item, None, ()) is True


# ===========================================================================
# Fallback API -> HTML
# ===========================================================================

class TestFallbackApiHtml:
    def test_api_con_productos_no_llama_al_fallback_html(self, requests_mock, monkeypatch):
        scraper = make_scraper(woocommerce_category_slug="one-piece")
        requests_mock.get(
            "https://tienda.example/wp-json/wc/store/v1/products",
            json=[api_item()],
        )
        mock_html = MagicMock(return_value=[])
        monkeypatch.setattr(scraper, "_scrape_via_html", mock_html)

        products = scraper.scrape()

        assert len(products) == 1
        mock_html.assert_not_called()

    def test_api_devuelve_lista_vacia_prueba_fallback_html(self, requests_mock):
        scraper = make_scraper(woocommerce_category_slug="one-piece")
        requests_mock.get("https://tienda.example/wp-json/wc/store/v1/products", json=[])
        requests_mock.get("https://tienda.example/wp-json/wc/store/products", json=[])
        html = """
        <li class="product">
            <a class="woocommerce-LoopProduct-link" href="https://tienda.example/producto/x">
                <h2 class="woocommerce-loop-product__title">Producto del fallback</h2>
            </a>
            <span class="price">19,95€</span>
        </li>
        """
        requests_mock.get("https://tienda.example/categoria-producto/one-piece/", text=html)

        products = scraper.scrape()

        assert len(products) == 1
        assert products[0].name == "Producto del fallback"

    def test_api_no_disponible_404_en_ambos_endpoints_usa_fallback(self, requests_mock):
        scraper = make_scraper(woocommerce_category_slug="one-piece")
        requests_mock.get("https://tienda.example/wp-json/wc/store/v1/products", status_code=404)
        requests_mock.get("https://tienda.example/wp-json/wc/store/products", status_code=404)
        requests_mock.get("https://tienda.example/categoria-producto/one-piece/", status_code=500)

        products = scraper.scrape()

        assert products == []  # ambos fallan -- lista vacía, sin excepción

    def test_sin_fallback_paths_y_api_vacia_no_intenta_html(self, requests_mock, monkeypatch):
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.WOOCOMMERCE,
                           woocommerce_category_slug="one-piece")
        logger = StoreLogger("Tienda", {})
        scraper = WooCommerceScraper(cfg, logger, delay=0)
        requests_mock.get("https://tienda.example/wp-json/wc/store/v1/products", json=[])
        requests_mock.get("https://tienda.example/wp-json/wc/store/products", json=[])
        mock_html = MagicMock()
        monkeypatch.setattr(scraper, "_scrape_via_html", mock_html)

        products = scraper.scrape()

        assert products == []
        mock_html.assert_not_called()


# ===========================================================================
# Paginación de la Store API
# ===========================================================================

class TestPaginacionStoreApi:
    def test_x_wp_total_pages_pagina_exactamente_esas_veces(self, requests_mock):
        # Cada página con per_page(=100) items exactos -- si tuviera menos,
        # el corte por "página corta" dispararía antes de que X-WP-TotalPages
        # llegara a importar (ver el propio orden de checks en _paginate_api).
        scraper = make_scraper(woocommerce_category_slug="one-piece")
        page1 = [api_item(id_=i) for i in range(100)]
        page2 = [api_item(id_=100 + i) for i in range(100)]
        requests_mock.get(
            "https://tienda.example/wp-json/wc/store/v1/products",
            [
                {"json": page1, "headers": {"X-WP-TotalPages": "2"}},
                {"json": page2, "headers": {"X-WP-TotalPages": "2"}},
            ],
        )
        products = scraper.scrape()
        assert len(products) == 200
        assert requests_mock.call_count == 2

    def test_pagina_con_menos_de_per_page_detiene_sin_totalpages(self, requests_mock):
        scraper = make_scraper(woocommerce_category_slug="one-piece")
        requests_mock.get(
            "https://tienda.example/wp-json/wc/store/v1/products",
            json=[api_item(id_=1)],  # 1 item < per_page(100), sin X-WP-TotalPages
        )
        products = scraper.scrape()
        assert len(products) == 1
        assert requests_mock.call_count == 1

    def test_sin_scoping_el_corte_de_20_paginas_es_codigo_inalcanzable(self, requests_mock):
        """HALLAZGO al escribir este test (no un caso feliz): la protección
        `if not has_scope and page >= 20 and not products: break` de
        _paginate_api es código MUERTO tal como está escrito hoy.

        has_scope (en _paginate_api) y el early-return de _product_in_scope
        usan EXACTAMENTE la misma condición ("not category_slug and not
        name_must_include"). Así que cuando has_scope es False,
        _product_in_scope acepta CUALQUIER item incondicionalmente -- por
        construcción, `products` nunca puede quedarse vacío durante 20
        páginas con datos, así que la condición `not products` de la
        protección nunca se cumple. La primera versión de este test
        reproducía justo eso: sin ningún corte real, requests-mock repite su
        última respuesta para siempre y el test entraba en un bucle
        infinito real (parado a mano, no un fallo de aserción).

        Como además StoreConfig exige scoping desde el fix de Arte9/ZIAL,
        `has_scope=False` ya ni siquiera es alcanzable en producción por la
        vía normal -- solo llamando a _paginate_api() a pelo, como aquí.
        Este test documenta el hallazgo con una única página (sin arriesgar
        otro bucle), no intenta forzar el corte de 20 páginas que no puede
        dispararse."""
        scraper = make_scraper(woocommerce_category_slug="one-piece")  # config válida cualquiera
        requests_mock.get(
            "https://tienda.example/wp-json/wc/store/v1/products",
            json=[api_item(id_=1, category_slug="otra-cosa")],  # 1 solo item, categoría "no relevante"
        )

        import requests as requests_lib
        session = requests_lib.Session()
        products, endpoint_ok = scraper._paginate_api(
            session, "/wp-json/wc/store/v1/products", category_slug=None, name_must_include=(),
        )

        # El item de categoría "otra-cosa" SÍ se acepta pese a no tener
        # relación con el scoping esperado -- justo lo que demuestra que la
        # protección de 20 páginas nunca vería products vacío.
        assert len(products) == 1
        assert endpoint_ok is True
        assert requests_mock.call_count == 1  # se corta por "página corta" (1 < per_page), no por el límite de 20


# ===========================================================================
# Parseo HTML defensivo: _clean_leaked_markup / _extract_price
# ===========================================================================

class TestWooCommerceRefreshProduct:
    def test_sin_store_sku_es_not_supported(self):
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.WOOCOMMERCE,
                           woocommerce_category_slug="one-piece")
        outcome = WooCommerceScraper.refresh_product(cfg, "https://tienda.example/producto/1")
        assert outcome.status == "not_supported"

    def test_con_store_sku_consulta_store_api_por_id(self, requests_mock):
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.WOOCOMMERCE,
                           woocommerce_category_slug="one-piece")
        requests_mock.get(
            "https://tienda.example/wp-json/wc/store/v1/products/42",
            json={"name": "Booster OP16", "is_in_stock": True,
                  "prices": {"price": "1995", "currency_minor_unit": 2}},
        )
        outcome = WooCommerceScraper.refresh_product(cfg, "https://tienda.example/producto/1", store_sku="42")
        assert outcome.status == "modified"
        assert outcome.variants[0].price == 19.95
        assert outcome.variants[0].name == "Booster OP16"

    def test_304_not_modified(self, requests_mock):
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.WOOCOMMERCE,
                           woocommerce_category_slug="one-piece")
        requests_mock.get("https://tienda.example/wp-json/wc/store/v1/products/42", status_code=304)
        outcome = WooCommerceScraper.refresh_product(cfg, "https://tienda.example/producto/1", store_sku="42")
        assert outcome.status == "not_modified"

    def test_404_es_error(self, requests_mock):
        cfg = StoreConfig("Tienda", "https://tienda.example", Platform.WOOCOMMERCE,
                           woocommerce_category_slug="one-piece")
        requests_mock.get("https://tienda.example/wp-json/wc/store/v1/products/42", status_code=404)
        outcome = WooCommerceScraper.refresh_product(cfg, "https://tienda.example/producto/1", store_sku="42")
        assert outcome.status == "error"


class TestCleanLeakedMarkup:
    def test_texto_con_html_escapado_filtrado_se_corta(self):
        # Caso real de Arte9: título contaminado con el cierre de </h2> y
        # spans hermanos, desescapados por BeautifulSoup como texto plano.
        texto = "CAJA A FIST OF DIVINE SPEED &# . . .</h2><span class=\"autor\">One Piece</span>"
        assert WooCommerceScraper._clean_leaked_markup(texto) == "CAJA A FIST OF DIVINE SPEED &# . . ."

    def test_texto_normal_sin_marcado_no_cambia(self):
        texto = "Booster Box OP-16 The Time of Battle"
        assert WooCommerceScraper._clean_leaked_markup(texto) == texto

    def test_none_no_revienta(self):
        assert WooCommerceScraper._clean_leaked_markup(None) is None


class TestExtractPrice:
    def _item(self, html):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").select_one("li")

    def test_oferta_estandar_ins_bdi_no_el_tachado(self):
        html = """<li><span class="price">
            <del><span class="amount"><bdi>25,00&nbsp;€</bdi></span></del>
            <ins><span class="amount"><bdi>20,00&nbsp;€</bdi></span></ins>
        </span></li>"""
        assert WooCommerceScraper._extract_price(self._item(html)) == 20.0

    def test_amount_unico_sin_oferta(self):
        html = '<li><span class="price"><span class="amount"><bdi>12,95&nbsp;€</bdi></span></span></li>'
        assert WooCommerceScraper._extract_price(self._item(html)) == 12.95

    def test_varios_importes_concatenados_sin_ins_del_coge_el_ultimo(self):
        # Caso real de Arte9: tachado + descuento + precio final, todos
        # spans hermanos sin marcado <ins>/<del>.
        html = """<li><span class="price">
            <span class="tachado">110,99€</span><span>-10%</span><span>99,89€</span>
        </span></li>"""
        assert WooCommerceScraper._extract_price(self._item(html)) == 99.89

    def test_sin_price_tag_devuelve_none(self):
        html = "<li><span>sin precio aquí</span></li>"
        assert WooCommerceScraper._extract_price(self._item(html)) is None


class TestParseHtmlPage:
    def test_paginacion_con_enlace_next_page_numbers(self):
        html = """
        <li class="product">
            <a class="woocommerce-LoopProduct-link" href="https://x.test/p1">
                <h2 class="woocommerce-loop-product__title">Producto 1</h2>
            </a>
            <span class="price">10,00€</span>
        </li>
        <a class="next page-numbers" href="https://x.test/pagina/2">Siguiente</a>
        """
        items, next_url = WooCommerceScraper._parse_html_page(html)
        assert len(items) == 1
        assert next_url == "https://x.test/pagina/2"

    def test_sin_enlace_siguiente_next_url_es_none(self):
        html = """
        <li class="product">
            <a class="woocommerce-LoopProduct-link" href="https://x.test/p1">
                <h2 class="woocommerce-loop-product__title">Producto 1</h2>
            </a>
        </li>
        """
        _items, next_url = WooCommerceScraper._parse_html_page(html)
        assert next_url is None

    def test_clase_outofstock_marca_agotado(self):
        html = """
        <li class="product outofstock">
            <a class="woocommerce-LoopProduct-link" href="https://x.test/p1">
                <h2 class="woocommerce-loop-product__title">Producto agotado</h2>
            </a>
        </li>
        """
        items, _next_url = WooCommerceScraper._parse_html_page(html)
        assert items[0]["stock_status"] == "AGOTADO"

    def test_sin_clase_outofstock_marca_disponible(self):
        html = """
        <li class="product instock">
            <a class="woocommerce-LoopProduct-link" href="https://x.test/p1">
                <h2 class="woocommerce-loop-product__title">Producto disponible</h2>
            </a>
        </li>
        """
        items, _next_url = WooCommerceScraper._parse_html_page(html)
        assert items[0]["stock_status"] == "DISPONIBLE"

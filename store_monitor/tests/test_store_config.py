"""Tests de StoreConfig.__post_init__() -- sección 1.3 del plan de pruebas.

Es la corrección estructural del bug real de Arte9/ZIAL (tienda WooCommerce
sin scoping, se traía todo el catálogo de la tienda). Estos tests confirman
que la validación SIGUE fallando en configuración incompleta -- no solo que
funciona en el camino feliz."""

from __future__ import annotations

import pytest

from base_script import Platform, StoreConfig


class TestShopifyValidation:
    def test_sin_shopify_collection_falla(self):
        with pytest.raises(ValueError, match="shopify_collection"):
            StoreConfig("Tienda", "https://tienda.com", Platform.SHOPIFY)

    def test_con_shopify_collection_construye_sin_error(self):
        cfg = StoreConfig("Tienda", "https://tienda.com", Platform.SHOPIFY, shopify_collection="one-piece")
        assert cfg.shopify_collection == "one-piece"


class TestPrestaShopValidation:
    def test_sin_category_url_falla(self):
        with pytest.raises(ValueError, match="prestashop_category_url"):
            StoreConfig("Tienda", "https://tienda.com", Platform.PRESTASHOP)


class TestOdooValidation:
    def test_sin_category_url_falla(self):
        with pytest.raises(ValueError, match="odoo_category_url"):
            StoreConfig("Tienda", "https://tienda.com", Platform.ODOO)


class TestOpenCartValidation:
    def test_sin_category_url_falla(self):
        with pytest.raises(ValueError, match="opencart_category_url"):
            StoreConfig("Tienda", "https://tienda.com", Platform.OPENCART)


class TestGenericJsonLdValidation:
    def test_sin_listing_urls_falla(self):
        with pytest.raises(ValueError, match="jsonld_listing_urls"):
            StoreConfig("Tienda", "https://tienda.com", Platform.GENERIC_JSONLD,
                        jsonld_product_link_selector="a.producto")

    def test_sin_link_selector_falla(self):
        with pytest.raises(ValueError, match="jsonld_product_link_selector"):
            StoreConfig("Tienda", "https://tienda.com", Platform.GENERIC_JSONLD,
                        jsonld_listing_urls=("https://tienda.com/cat",))

    def test_con_ambos_construye_sin_error(self):
        cfg = StoreConfig("Tienda", "https://tienda.com", Platform.GENERIC_JSONLD,
                           jsonld_listing_urls=("https://tienda.com/cat",),
                           jsonld_product_link_selector="a.producto")
        assert cfg.jsonld_listing_urls == ("https://tienda.com/cat",)


class TestWooCommerceValidation:
    """El caso Arte9/ZIAL: sin NINGÚN mecanismo de scoping, el scraper se
    traería todo el catálogo de la tienda."""

    def test_sin_scoping_ni_fallback_falla_mencionando_ambos_campos_de_scoping(self):
        with pytest.raises(ValueError) as exc_info:
            StoreConfig("Tienda", "https://tienda.com", Platform.WOOCOMMERCE)
        message = str(exc_info.value)
        assert "woocommerce_category_slug" in message
        assert "woocommerce_name_must_include" in message

    def test_name_must_include_sin_ninguna_ruta_de_fetch_falla(self):
        # Scoping por nombre configurado, pero sin category_slug NI
        # fallback_paths -- no hay ninguna ruta HTML que recorrer y filtrar.
        with pytest.raises(ValueError, match="fallback_path"):
            StoreConfig("Tienda", "https://tienda.com", Platform.WOOCOMMERCE,
                        woocommerce_name_must_include=("one piece",))

    def test_category_slug_y_fallback_paths_construye_sin_error(self):
        cfg = StoreConfig("Tienda", "https://tienda.com", Platform.WOOCOMMERCE,
                           woocommerce_category_slug="op",
                           woocommerce_fallback_paths=("product-category/op",))
        assert cfg.woocommerce_category_slug == "op"

    def test_solo_category_slug_basta_tambien_como_ruta_de_fetch(self):
        # category_slug sirve a la vez como scoping Y como ruta de fetch
        # (vía Store API) -- no hace falta fallback_paths si hay category_slug.
        cfg = StoreConfig("Tienda", "https://tienda.com", Platform.WOOCOMMERCE,
                           woocommerce_category_slug="op")
        assert cfg.woocommerce_category_slug == "op"

    def test_name_must_include_con_fallback_paths_construye_sin_error(self):
        # Caso real: El Encuentro (sin categoría estándar, scoping por nombre).
        cfg = StoreConfig("Tienda", "https://tienda.com", Platform.WOOCOMMERCE,
                           woocommerce_name_must_include=("one piece",),
                           woocommerce_fallback_paths=("tienda/categoria/tcg",))
        assert cfg.woocommerce_name_must_include == ("one piece",)


class TestDomainNormalization:
    def test_barra_final_se_elimina(self):
        cfg = StoreConfig("Tienda", "https://tienda.com/", Platform.SHOPIFY, shopify_collection="x")
        assert cfg.domain == "https://tienda.com"

    def test_sin_barra_final_no_cambia(self):
        cfg = StoreConfig("Tienda", "https://tienda.com", Platform.SHOPIFY, shopify_collection="x")
        assert cfg.domain == "https://tienda.com"

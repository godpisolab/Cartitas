from domain import Platform

from scrapers.base import BaseStoreScraper
from scrapers.generic_jsonld import GenericJsonLdScraper
from scrapers.odoo import OdooScraper
from scrapers.opencart import OpenCartScraper
from scrapers.prestashop import PrestaShopScraper
from scrapers.shopify import ShopifyScraper
from scrapers.woocommerce import WooCommerceScraper

# Registro plataforma -> clase de scraper. Vive aquí (no en dispatcher.py) a
# propósito: persistence.py (refresh_hot_products, E.2) también lo necesita,
# y persistencia no puede depender del dispatcher (ver
# docs/estandares_organizacion_codigo.md, sección 2) -- pero sí puede
# depender de scrapers, que no depende de nada por encima de sí mismo.
SCRAPER_CLASSES: dict[Platform, type[BaseStoreScraper]] = {
    Platform.SHOPIFY: ShopifyScraper,
    Platform.PRESTASHOP: PrestaShopScraper,
    Platform.WOOCOMMERCE: WooCommerceScraper,
    Platform.ODOO: OdooScraper,
    Platform.OPENCART: OpenCartScraper,
    Platform.GENERIC_JSONLD: GenericJsonLdScraper,
}

__all__ = [
    "BaseStoreScraper",
    "ShopifyScraper",
    "PrestaShopScraper",
    "WooCommerceScraper",
    "OdooScraper",
    "OpenCartScraper",
    "GenericJsonLdScraper",
    "SCRAPER_CLASSES",
]

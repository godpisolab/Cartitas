from scrapers.base import BaseStoreScraper
from scrapers.generic_jsonld import GenericJsonLdScraper
from scrapers.odoo import OdooScraper
from scrapers.opencart import OpenCartScraper
from scrapers.prestashop import PrestaShopScraper
from scrapers.shopify import ShopifyScraper
from scrapers.woocommerce import WooCommerceScraper

__all__ = [
    "BaseStoreScraper",
    "ShopifyScraper",
    "PrestaShopScraper",
    "WooCommerceScraper",
    "OdooScraper",
    "OpenCartScraper",
    "GenericJsonLdScraper",
]

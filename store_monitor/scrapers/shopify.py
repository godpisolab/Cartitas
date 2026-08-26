"""Shopify: JSON público, no requiere HTML parsing."""

from __future__ import annotations

import time

from base_script import Product, build_session, parse_price_text, request_with_retries
from scrapers.base import BaseStoreScraper


class ShopifyScraper(BaseStoreScraper):
    """Toda tienda Shopify expone su catálogo por colección como JSON público
    en /collections/<handle>/products.json -- no hace falta ni sesión
    anti-bot ni parsear HTML."""

    def scrape(self) -> list[Product]:
        """Pagina products.json (limit=250) hasta que una página viene vacía
        -- shopify_collection es el handle configurado en StoreConfig."""
        session = build_session(anti_bot=False, config=self.config)
        products: list[Product] = []
        page = 1

        while True:
            url = f"{self.config.domain}/collections/{self.config.shopify_collection}/products.json"
            self.logger.log(f"solicitando página {page}...")
            resp = request_with_retries(session, url, params={"limit": 250, "page": page},
                                         heartbeat=self.logger.touch)

            if resp is None:
                self.logger.log(f"ERROR de red irrecuperable en página {page}, abortando esta tienda")
                break
            if resp.status_code != 200:
                self.logger.log(f"ERROR: página {page} devolvió {resp.status_code}, abortando")
                break
            try:
                data = resp.json()
            except ValueError:
                self.logger.log(f"ERROR: página {page} no devolvió JSON válido, abortando")
                break

            raw_products = data.get("products", [])
            if not raw_products:
                break

            self.logger.log(f"página {page}: {len(raw_products)} productos (acumulado {len(products)})")

            for product in raw_products:
                products.extend(self._products_from_item(product))

            page += 1
            time.sleep(self.delay)

        return products

    def _products_from_item(self, product: dict) -> list[Product]:
        """Un producto de Shopify puede tener varias variantes (idioma,
        edición...) con stock independiente -- se genera una fila de Product
        POR VARIANTE, no una por producto, para no perder esa granularidad."""
        name = product.get("title")
        handle = product.get("handle")
        images = product.get("images") or [{}]
        image_url = images[0].get("src") if images else None
        variants = product.get("variants") or [{}]

        # Aviso: si un producto tiene variantes con disponibilidad distinta
        # (p.ej. Inglés agotado / Japonés disponible), el producto SÍ está
        # comprable en conjunto -- el CSV se tiene que mirar a nivel de
        # variante (columna 'variant'), no a nivel de producto.
        if len(variants) > 1 and len({v.get("available") for v in variants}) > 1:
            self.logger.log(f"AVISO: '{name}' tiene variantes con stock mixto "
                             f"({len(variants)} variantes) -- revisa la columna 'variant'")

        return [
            self._make_product(
                id_product=product.get("id"),
                name=name,
                price=parse_price_text(variant.get("price")),
                stock_status="DISPONIBLE" if variant.get("available") else "AGOTADO",
                url=f"{self.config.domain}/products/{handle}",
                sku=variant.get("sku"),
                image_url=image_url,
                variant_title=variant.get("title"),
            )
            for variant in variants
        ]

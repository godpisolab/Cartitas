"""Shopify: JSON público, no requiere HTML parsing."""

from __future__ import annotations

import time
from typing import Optional

from shared.classify import parse_price_text
from shared.domain import Product, RefreshedVariant, RefreshOutcome, StoreConfig
from http_client import build_session, conditional_headers, request_with_retries
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

    @classmethod
    def refresh_product(cls, config: StoreConfig, store_url: str, *, store_sku: Optional[str] = None,
                         etag: Optional[str] = None, last_modified: Optional[str] = None) -> RefreshOutcome:
        """E.2: el propio store_url + '.json' es el endpoint de detalle
        (mismo host que products.json, Shopify siempre lo expone así). Una
        sola petición devuelve TODAS las variantes de golpe -- si el
        producto tiene varias (idioma, sobre/caja...), se devuelve una
        RefreshedVariant por cada una, y el llamador empareja por el título
        exacto de variante contra el raw_variant ya guardado."""
        session = build_session(anti_bot=False, config=config)
        resp = request_with_retries(
            session, f"{store_url}.json",
            headers=conditional_headers(etag, last_modified),
        )

        if resp is None:
            return RefreshOutcome(status="error", error="fallo de red irrecuperable")
        if resp.status_code == 304:
            return RefreshOutcome(status="not_modified")
        if resp.status_code != 200:
            return RefreshOutcome(status="error", error=f"status {resp.status_code}")

        try:
            data = resp.json()
        except ValueError:
            return RefreshOutcome(status="error", error="respuesta no es JSON válido")

        product = data.get("product") or {}
        variants = product.get("variants") or []
        if not variants:
            return RefreshOutcome(status="error", error="sin variantes en la respuesta")

        return RefreshOutcome(
            status="modified",
            variants=[
                RefreshedVariant(
                    variant=v.get("title"),
                    price=parse_price_text(v.get("price")),
                    stock_status="DISPONIBLE" if v.get("available") else "AGOTADO",
                    name=product.get("title"),
                )
                for v in variants
            ],
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
        )

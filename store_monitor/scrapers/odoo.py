"""Odoo: HTML server-side del módulo website_sale."""

from __future__ import annotations

import json
import time
from typing import Optional

from bs4 import BeautifulSoup

from classify import parse_price_text
from domain import Product, RefreshedVariant, RefreshOutcome, StoreConfig
from http_client import build_session, conditional_headers, request_with_retries
from scrapers.base import BaseStoreScraper

MAX_LISTING_PAGES = 50  # cinturón de seguridad ante una paginación mal detectada/circular


class OdooScraper(BaseStoreScraper):
    """✅ Confirmado contra el HTML real de TCG Legacy (una tienda Odoo 19).

    ESTRATEGIA (dos fases, verificada con datos reales):

    1. Recorre el listado de categoría para recolectar las URLs de cada
       producto (selectores confirmados: `.oe_product_cart` dentro de
       `.o_wsale_product_grid_wrapper` -- se ve en el propio CSS
       personalizado de la tienda, comentado literalmente como "clases
       reales Odoo 19").

    2. Visita CADA página de producto individual y lee el bloque
       `<script type="application/ld+json">` con `"@type": "Product"` que
       Odoo genera automáticamente para SEO (schema.org). Es la fuente de
       verdad para precio y, sobre todo, disponibilidad
       (`offers.availability`: ".../InStock" vs ".../OutOfStock").

    POR QUÉ NO SE PUEDE SABER EL STOCK DESDE EL LISTADO: en esta tienda el
    control de inventario se resuelve en el CLIENTE vía JavaScript --
    el HTML servido por el servidor no contiene ninguna marca estática de
    "agotado" (ni clase CSS, ni ribbon: el ribbon visible en la página de
    producto, p.ej. "Disponible en tienda", es un rótulo personalizado sin
    relación con el stock online, NO uses ribbons como señal de stock en
    Odoo salvo que confirmes su significado caso por caso). El JSON-LD es la
    única señal de disponibilidad presente en el HTML estático.

    COSTE: esto significa una petición por producto además del listado
    (N+1), más lento que las otras plataformas -- inevitable dado cómo
    expone esta tienda la disponibilidad."""

    def scrape(self) -> list[Product]:
        """Fase 1 + fase 2 (ver docstring de la clase): recolecta URLs de
        producto y visita cada una para leer su JSON-LD. Un producto sin
        JSON-LD válido se omite con un AVISO en vez de abortar toda la
        tienda -- puede ser una página con tema distinto, no necesariamente
        un fallo general."""
        session = build_session(anti_bot=True, config=self.config)
        self.logger.log("iniciando sesión anti-bot (cloudscraper)...")

        product_urls = self._collect_product_urls(session)
        self.logger.log(f"{len(product_urls)} URLs de producto encontradas en el listado, "
                         f"visitando cada una para leer precio/disponibilidad reales (JSON-LD)...")

        products: list[Product] = []
        for i, url in enumerate(product_urls, start=1):
            self.logger.log(f"producto {i}/{len(product_urls)}: {url}")
            resp = request_with_retries(session, url, heartbeat=self.logger.touch)
            if resp is None or resp.status_code != 200:
                status = getattr(resp, "status_code", None)
                self.logger.log(f"ERROR en {url} (status={status}), se omite este producto")
                continue

            item = self._parse_product_jsonld(resp.text)
            if item is None:
                self.logger.log(f"AVISO: sin JSON-LD de tipo Product en {url}, se omite "
                                 f"(la tienda pudo cambiar de tema)")
                continue

            products.append(self._make_product(
                id_product=item.get("product_id"),
                name=item["name"],
                price=item["price"],
                stock_status=item["stock_status"],
                url=url,
                sku=item.get("gtin"),
                image_url=item.get("image"),
            ))

            time.sleep(self.delay)

        return products

    # -- Fase 1: listado -> URLs de producto -----------------------------

    def _collect_product_urls(self, session) -> list[str]:
        """Pagina el listado de categoría recolectando URLs de producto
        (deduplicadas). Se corta si una página no aporta ninguna URL nueva
        (protección contra el bucle infinito real que se dio en Estalia
        Córdoba: esa tienda re-sirve la última página real para siempre y su
        widget de paginación nunca indica el final) o al llegar a
        MAX_LISTING_PAGES como cinturón de seguridad adicional."""
        urls: list[str] = []
        seen: set[str] = set()
        page = 1
        category_url = self.config.odoo_category_url

        while page <= MAX_LISTING_PAGES:
            url = category_url if page == 1 else f"{category_url}/page/{page}"
            self.logger.log(f"solicitando listado página {page}...")
            resp = request_with_retries(session, url, heartbeat=self.logger.touch)
            if resp is None or resp.status_code != 200:
                status = getattr(resp, "status_code", None)
                self.logger.log(f"ERROR en listado página {page} (status={status}), abortando listado")
                break

            page_urls, has_next = self._parse_grid_page(resp.text)
            if not page_urls:
                if page == 1:
                    self.logger.log("AVISO: 0 productos en la primera página del listado -- "
                                     "revisar selectores de _parse_grid_page si esto persiste")
                break

            new_in_page = 0
            for u in page_urls:
                absolute_url = u if u.startswith("http") else f"{self.config.domain}{u}"
                if absolute_url not in seen:
                    seen.add(absolute_url)
                    urls.append(absolute_url)
                    new_in_page += 1

            # Verificado en Estalia Córdoba (2026-08-25): al pasarse de la
            # última página real, esta tienda no da 404 ni dice has_next=False
            # -- re-sirve el contenido de la última página indefinidamente
            # (el widget de paginación queda "activo" para siempre). Sin este
            # corte, el scraper entraría en un bucle infinito real: como cada
            # petición exitosa cuenta como "actividad", el timeout por
            # inactividad de StoreLogger nunca se dispara para detectarlo.
            if new_in_page == 0:
                self.logger.log(f"AVISO: página {page} no aportó productos nuevos -- la tienda "
                                 f"probablemente repite la última página real, deteniendo paginación")
                break

            if not has_next:
                break
            page += 1
            time.sleep(self.delay)
        else:
            self.logger.log(f"AVISO: se alcanzó el límite de seguridad de {MAX_LISTING_PAGES} páginas "
                             f"sin que has_next indicara el final -- revisar paginación a mano")

        return urls

    @staticmethod
    def _parse_grid_page(html: str) -> tuple[list[str], bool]:
        """Selectores confirmados contra TCG Legacy (Odoo 19): las tarjetas
        de producto son `.oe_product_cart`, normalmente dentro de
        `.o_wsale_product_grid_wrapper`. Se mantiene `.oe_product` como
        fallback por si otra tienda corre una versión distinta de Odoo con
        el marcado más antiguo."""
        soup = BeautifulSoup(html, "html.parser")
        urls: list[str] = []

        cards = soup.select(
            ".o_wsale_product_grid_wrapper .oe_product_cart, .oe_product_cart, "
            ".o_wsale_product_grid_wrapper .oe_product, div.oe_product"
        )
        for card in cards:
            link_tag = card.select_one("a[itemprop='url'], .o_wsale_products_item_title a, a")
            if link_tag and link_tag.has_attr("href"):
                urls.append(link_tag["href"])

        has_next = soup.select_one(
            "nav.o_website_sale_pager a[rel='next'], .pagination a[rel='next'], "
            ".pagination li.active + li a"
        ) is not None
        return urls, has_next

    @classmethod
    def refresh_product(cls, config: StoreConfig, store_url: str, *, store_sku: Optional[str] = None,
                         etag: Optional[str] = None, last_modified: Optional[str] = None) -> RefreshOutcome:
        """E.2: OdooScraper YA visita la ficha individual como parte de su
        barrido normal (ver docstring de la clase) -- el refresco solo
        repite esa misma petición puntual y reutiliza _parse_product_jsonld
        tal cual, sin duplicar la lógica de extracción."""
        session = build_session(anti_bot=True, config=config)
        resp = request_with_retries(session, store_url, headers=conditional_headers(etag, last_modified))

        if resp is None:
            return RefreshOutcome(status="error", error="fallo de red irrecuperable")
        if resp.status_code == 304:
            return RefreshOutcome(status="not_modified")
        if resp.status_code != 200:
            return RefreshOutcome(status="error", error=f"status {resp.status_code}")

        item = cls._parse_product_jsonld(resp.text)
        if item is None:
            return RefreshOutcome(status="error", error="sin JSON-LD de tipo Product en la ficha")

        return RefreshOutcome(
            status="modified",
            variants=[RefreshedVariant(variant=None, price=item["price"], stock_status=item["stock_status"],
                                        name=item["name"])],
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
        )

    # -- Fase 2: página de producto -> JSON-LD (fuente de verdad) --------

    @staticmethod
    def _parse_product_jsonld(html: str) -> Optional[dict]:
        """Busca, entre todos los <script type="application/ld+json"> de la
        página (puede haber varios: Organization, Product+BreadcrumbList
        como array, LocalBusiness+WebSite como @graph...), el que tiene
        "@type": "Product", y extrae nombre/precio/disponibilidad/gtin.

        El campo `offers.availability` es una URL de schema.org
        (https://schema.org/InStock, .../OutOfStock, .../LimitedAvailability,
        .../PreOrder, .../Discontinued...). Se considera AGOTADO solo
        "OutOfStock" y "Discontinued" -- el resto de valores implican que
        SÍ se puede comprar de alguna forma."""
        soup = BeautifulSoup(html, "html.parser")

        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue

            candidates = data if isinstance(data, list) else [data]
            for entry in candidates:
                if not isinstance(entry, dict) or entry.get("@type") != "Product":
                    continue

                offers = entry.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}

                availability = str(offers.get("availability") or "").lower()
                stock_status = (
                    "AGOTADO" if ("outofstock" in availability or "discontinued" in availability)
                    else "DISPONIBLE"
                )

                return {
                    "name": entry.get("name"),
                    "price": parse_price_text(offers.get("price")),
                    "stock_status": stock_status,
                    "image": entry.get("image"),
                    "gtin": entry.get("gtin") or entry.get("sku"),
                    "product_id": None,
                }

        return None

"""Genérico JSON-LD: para tiendas con CMS a medida que no encajan en ninguna
de las 5 plataformas soportadas (Shopify/PrestaShop/WooCommerce/Odoo/
OpenCart), pero que exponen JSON-LD schema.org de tipo Product en sus páginas
de producto -- mismo patrón que OdooScraper (listado -> recolectar URLs de
producto -> visitar cada una -> leer JSON-LD), pero con el listado
parametrizado por StoreConfig en vez de selectores fijos, porque cada CMS
custom pagina y estructura su listado de forma distinta.

Verificado contra 3 tiendas reales (2026-08-25): NIKOCHAN ARENA (JSON-LD
limpio, offers.availability directo), ISEKAI (offers anidado en un
AggregateOffer -- offers.offers.availability, y "@type" en minúscula) y Comic
Stores/Freak Point (JSON-LD sin availability, hay que leerla de un
span/meta aparte)."""

from __future__ import annotations

import json
import time
from typing import Optional

from bs4 import BeautifulSoup

from base_script import Product, build_session, parse_price_text, request_with_retries
from scrapers.base import BaseStoreScraper

MAX_LISTING_PAGES = 50  # cinturón de seguridad ante una paginación mal detectada/circular

PAGINATION_SELECTOR = "a[rel='next'], .pagination a, .paginacion a, .page-numbers"


class GenericJsonLdScraper(BaseStoreScraper):
    """Mismo patrón que OdooScraper (listado -> recolectar URLs -> visitar
    cada producto -> leer JSON-LD) pero con el listado parametrizado en
    StoreConfig, para CMS a medida que no encajan en ninguna de las otras
    4 plataformas. Ver docstring del módulo para el detalle de las 3 tiendas
    verificadas."""

    def scrape(self) -> list[Product]:
        """Fase 1 + fase 2: recolecta URLs de producto y visita cada una
        para leer su JSON-LD (con los fallbacks de _parse_product_page). Un
        producto sin JSON-LD válido se omite con un AVISO, no aborta la
        tienda entera."""
        session = build_session(anti_bot=True, config=self.config)
        self.logger.log("iniciando sesión anti-bot (cloudscraper)...")

        product_urls = self._collect_product_urls(session)
        self.logger.log(f"{len(product_urls)} URLs de producto encontradas en el/los listado(s), "
                         f"visitando cada una para leer JSON-LD...")

        products: list[Product] = []
        for i, url in enumerate(product_urls, start=1):
            self.logger.log(f"producto {i}/{len(product_urls)}: {url}")
            resp = request_with_retries(session, url, heartbeat=self.logger.touch)
            if resp is None or resp.status_code != 200:
                status = getattr(resp, "status_code", None)
                self.logger.log(f"ERROR en {url} (status={status}), se omite este producto")
                continue

            item = self._parse_product_page(resp.text)
            if item is None:
                self.logger.log(f"AVISO: sin JSON-LD de tipo Product en {url}, se omite "
                                 f"(la tienda pudo cambiar de tema)")
                continue

            products.append(self._make_product(
                id_product=item.get("sku"),
                name=item["name"],
                price=item["price"],
                stock_status=item["stock_status"],
                url=url,
                sku=item.get("sku"),
                image_url=item.get("image"),
            ))

            time.sleep(self.delay)

        return products

    # -- Fase 1: listado(s) -> URLs de producto --------------------------
    #
    # No se construye ninguna URL de página a mano: se parte de
    # jsonld_listing_urls y se sigue CUALQUIER enlace de paginación real que
    # aparezca (numerado o "siguiente"), acumulando páginas nuevas hasta
    # agotarlas. Esto funciona igual para una categoría (ISEKAI, con
    # paginación numerada + rel=next) que para resultados de un buscador
    # interno (Comic Stores, con paginación solo numerada), sin lógica
    # distinta por tienda.

    def _collect_product_urls(self, session) -> list[str]:
        """BFS sobre jsonld_listing_urls: extrae enlaces de producto
        (jsonld_product_link_selector) de cada página visitada, y añade a la
        cola cualquier enlace de paginación real que encuentre (ver
        PAGINATION_SELECTOR) -- nunca construye una URL de página a mano.
        Limitado por MAX_LISTING_PAGES como cinturón de seguridad."""
        urls: list[str] = []
        seen_urls: set[str] = set()
        link_selector = self.config.jsonld_product_link_selector

        pages_to_visit = list(self.config.jsonld_listing_urls)
        visited_pages: set[str] = set()

        while pages_to_visit and len(visited_pages) < MAX_LISTING_PAGES:
            page_url = pages_to_visit.pop(0)
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)

            self.logger.log(f"solicitando listado {page_url}...")
            resp = request_with_retries(session, page_url, heartbeat=self.logger.touch)
            if resp is None or resp.status_code != 200:
                status = getattr(resp, "status_code", None)
                self.logger.log(f"ERROR en listado {page_url} (status={status}), se omite esta página")
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            page_links = 0
            for a in soup.select(link_selector):
                href = a.get("href")
                if not href:
                    continue
                absolute_url = href if href.startswith("http") else f"{self.config.domain}{href}"
                if absolute_url not in seen_urls:
                    seen_urls.add(absolute_url)
                    urls.append(absolute_url)
                    page_links += 1

            self.logger.log(f"listado {page_url}: {page_links} productos nuevos (acumulado {len(urls)})")

            for a in soup.select(PAGINATION_SELECTOR):
                href = a.get("href")
                if not href or href == "#":
                    continue
                absolute_page_url = href if href.startswith("http") else f"{self.config.domain}{href}"
                if absolute_page_url not in visited_pages and absolute_page_url not in pages_to_visit:
                    pages_to_visit.append(absolute_page_url)

            time.sleep(self.delay)

        return urls

    # -- Fase 2: página de producto -> JSON-LD (+ fallback de stock) -----

    @staticmethod
    def _parse_product_page(html: str) -> Optional[dict]:
        """Busca el bloque JSON-LD de tipo Product (case-insensitive, admite
        @type en minúscula como en ISEKAI) y extrae nombre/precio/stock/sku/
        imagen. La disponibilidad se busca en orden: offers.availability,
        luego offers.offers.availability (AggregateOffer anidado, caso
        ISEKAI), luego un <span class="availability"> o una meta
        product:availability aparte (caso Comic Stores, cuyo JSON-LD no trae
        disponibilidad). Si no aparece en ningún sitio, DESCONOCIDO en vez de
        asumir DISPONIBLE a ciegas. Devuelve None si no hay JSON-LD de tipo
        Product en absoluto."""
        soup = BeautifulSoup(html, "html.parser")

        entry = None
        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue

            candidates = data if isinstance(data, list) else [data]
            for candidate in candidates:
                if isinstance(candidate, dict) and str(candidate.get("@type", "")).lower() == "product":
                    entry = candidate
                    break
            if entry:
                break

        if entry is None:
            return None

        offers = entry.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}

        # ISEKAI anida la oferta real un nivel más abajo dentro de un
        # AggregateOffer (offers.offers.price/availability en vez de
        # offers.price/availability directo) -- si el nivel exterior no trae
        # precio, se baja un nivel.
        inner_offers = offers.get("offers") or {}
        if isinstance(inner_offers, list):
            inner_offers = inner_offers[0] if inner_offers else {}
        price_source = inner_offers if (not offers.get("price") and inner_offers.get("price")) else offers

        availability = str(offers.get("availability") or inner_offers.get("availability") or "").lower()

        if not availability:
            # Comic Stores/Freak Point no incluye availability en el JSON-LD
            # -- se busca en un span de disponibilidad aparte (formato
            # schema.org limpio, "InStock"/"OutOfStock") y, si tampoco está,
            # en una meta tag (formato con espacios, "in stock"/"out of
            # stock", de ahí el .replace(" ", "") antes de comparar).
            span = soup.select_one("span.availability")
            if span:
                availability = span.get_text(strip=True).lower()
            else:
                meta = soup.find("meta", attrs={"property": "product:availability"})
                if meta and meta.get("content"):
                    availability = meta["content"].lower().replace(" ", "")

        if not availability:
            stock_status = "DESCONOCIDO"
        elif "outofstock" in availability or "discontinued" in availability:
            stock_status = "AGOTADO"
        else:
            stock_status = "DISPONIBLE"

        return {
            "name": entry.get("name"),
            "price": parse_price_text(price_source.get("price") or price_source.get("lowPrice")),
            "stock_status": stock_status,
            "image": entry.get("image"),
            "sku": entry.get("sku") or entry.get("gtin") or entry.get("gtin8") or entry.get("gtin12"),
        }

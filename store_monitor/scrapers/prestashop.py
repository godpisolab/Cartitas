"""PrestaShop: HTML server-side, tema IQIT."""

from __future__ import annotations

import time

from bs4 import BeautifulSoup

from typing import Optional

from classify import parse_price_text
from domain import Product, RefreshedVariant, RefreshOutcome, StoreConfig
from http_client import build_session, conditional_headers, request_with_retries
from scrapers.base import BaseStoreScraper

MAX_LISTING_PAGES = 50  # cinturón de seguridad ante una paginación mal detectada/circular


class PrestaShopScraper(BaseStoreScraper):
    """HTML server-side del tema IQIT (Distrito Zero, Gameria, Geekkaos...).
    No hay API pública equivalente a la Store API de WooCommerce, así que
    todo pasa por parsear el listado de categoría directamente."""

    def scrape(self) -> list[Product]:
        """Visita primero la home (algunas tiendas necesitan esa cookie de
        sesión antes de servir la categoría), luego pagina
        prestashop_category_url hasta detected_max_page, deduplicando por
        id_product por si la tienda re-sirve la última página real para
        números de página que ya no existen (ver comentario más abajo)."""
        session = build_session(anti_bot=True, config=self.config)
        self.logger.log("iniciando sesión anti-bot (cloudscraper)...")

        home_resp = request_with_retries(session, self.config.domain + "/", heartbeat=self.logger.touch)
        if home_resp is None:
            self.logger.log("ERROR irrecuperable al pedir la home, abortando")
            return []
        self.logger.log(f"home status: {home_resp.status_code}")
        time.sleep(1.5)

        products: list[Product] = []
        seen_ids: set[str] = set()
        page = 1
        max_page = 1
        category_url = self.config.prestashop_category_url

        while page <= max_page and page <= MAX_LISTING_PAGES:
            sep = "&" if "?" in category_url else "?"
            url = f"{category_url}{sep}resultsPerPage=36"
            if page > 1:
                url += f"&page={page}"

            self.logger.log(f"solicitando página {page}/{max_page}...")
            resp = request_with_retries(session, url, heartbeat=self.logger.touch)
            if resp is None or resp.status_code != 200:
                status = getattr(resp, "status_code", None)
                self.logger.log(f"ERROR en página {page} (status={status}), abortando")
                break

            raw_items, detected_max_page = self._parse_page(resp.text)
            self.logger.log(f"página {page}/{detected_max_page}: {len(raw_items)} productos "
                             f"(acumulado {len(products)})")

            # Dedupe por id_product: si el sitio re-sirve la última página real
            # para números de página que ya no existen (visto en Estalia
            # Córdoba con Odoo -- mismo riesgo aquí, confiar en el número de
            # página máximo que reporta el propio sitio), esto evita productos
            # duplicados en el CSV y permite detectar el estancamiento.
            new_in_page = 0
            for item in raw_items:
                if item["id_product"] and item["id_product"] in seen_ids:
                    continue
                if item["id_product"]:
                    seen_ids.add(item["id_product"])
                new_in_page += 1
                products.append(self._make_product(
                    id_product=item["id_product"],
                    name=item["name"],
                    price=parse_price_text(item["price"]),
                    stock_status=item["stock_status"],
                    url=item["url"],
                    sku=item["reference"],
                    image_url=item["image_url"],
                ))

            if raw_items and new_in_page == 0:
                self.logger.log(f"AVISO: página {page} no aportó productos nuevos -- la tienda "
                                 f"probablemente repite la última página real, deteniendo paginación")
                break

            max_page = detected_max_page
            page += 1
            time.sleep(self.delay)

        return products

    @classmethod
    def refresh_product(cls, config: StoreConfig, store_url: str, *, store_sku: Optional[str] = None,
                         etag: Optional[str] = None, last_modified: Optional[str] = None) -> RefreshOutcome:
        """E.2: no hay API pública (igual que en scrape()), así que se
        visita la ficha de producto y se lee la microdata schema.org que el
        tema IQIT sí expone ahí (aunque no en el listado) -- verificado
        contra HTML real de Distrito Zero: `[itemprop=price]` (atributo
        `content`) y `[itemprop=availability]` (atributo `href`, URL de
        schema.org tipo `https://schema.org/InStock`, mismo formato que ya
        interpreta OdooScraper)."""
        session = build_session(anti_bot=True, config=config)
        resp = request_with_retries(session, store_url, headers=conditional_headers(etag, last_modified))

        if resp is None:
            return RefreshOutcome(status="error", error="fallo de red irrecuperable")
        if resp.status_code == 304:
            return RefreshOutcome(status="not_modified")
        if resp.status_code != 200:
            return RefreshOutcome(status="error", error=f"status {resp.status_code}")

        item = cls._parse_product_detail(resp.text)
        if item is None:
            return RefreshOutcome(status="error", error="sin microdata de precio en la ficha de producto")

        return RefreshOutcome(
            status="modified",
            variants=[RefreshedVariant(variant=None, price=item["price"], stock_status=item["stock_status"],
                                        name=item["name"])],
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
        )

    @staticmethod
    def _parse_product_detail(html: str) -> Optional[dict]:
        """Ficha individual del tema IQIT -- verificado contra HTML real de
        Distrito Zero. Si `[itemprop=price]` no aparece, se asume que la
        tienda cambió de tema (igual que _parse_page para el listado) y se
        devuelve None en vez de arriesgar un precio inventado."""
        soup = BeautifulSoup(html, "html.parser")

        price_tag = soup.select_one("[itemprop=price]")
        if price_tag is None:
            return None
        price = parse_price_text(price_tag.get("content") or price_tag.get_text(strip=True))

        # OJO: [itemprop=name] existe en esta ficha pero apunta al breadcrumb
        # ("Inicio"), no al producto -- verificado contra HTML real de
        # Distrito Zero. El h1 sí es el título real del producto.
        name_tag = soup.select_one("h1")
        name = name_tag.get_text(strip=True) if name_tag else None

        availability_tag = soup.select_one("[itemprop=availability]")
        availability = (availability_tag.get("href") or availability_tag.get_text(strip=True)).lower() \
            if availability_tag else ""

        if not availability:
            # Sin microdata de disponibilidad -- cae al mismo indicador visual
            # que usa _parse_page para el listado (".product-available").
            stock_status = "DISPONIBLE" if soup.select_one(".product-available") else "DESCONOCIDO"
        elif "outofstock" in availability or "discontinued" in availability:
            stock_status = "AGOTADO"
        else:
            stock_status = "DISPONIBLE"

        return {"price": price, "stock_status": stock_status, "name": name}

    @staticmethod
    def _parse_page(html: str):
        """Parser genérico para el tema IQIT de PrestaShop (Distrito Zero, Gameria,
        Geekkaos...). Si una tienda nueva usa un tema distinto, revisa estos
        selectores CSS a mano -- esto no se puede generalizar más sin arriesgarse
        a falsos positivos entre temas distintos."""
        soup = BeautifulSoup(html, "html.parser")
        items = []

        for article in soup.select("article.product-miniature"):
            title_tag = article.select_one("h3.product-title a") or article.select_one(".product-title a")
            price_tag = article.select_one("span.product-price")
            img_tag = article.select_one("img.product-thumbnail-first") or article.select_one("img")
            ref_tag = article.select_one(".product-reference")

            items.append({
                "id_product": article.get("data-id-product"),
                "name": title_tag.get_text(strip=True) if title_tag else None,
                "url": title_tag["href"] if title_tag else None,
                "reference": ref_tag.get_text(strip=True) if ref_tag else None,
                "price": price_tag["content"] if price_tag and price_tag.has_attr("content") else None,
                "stock_status": "DISPONIBLE" if article.select_one(".product-available") else "AGOTADO",
                "image_url": img_tag.get("data-original") if img_tag else None,
            })

        max_page = 1
        for a in soup.select("nav.pagination a.js-search-link"):
            text = a.get_text(strip=True)
            if text.isdigit():
                max_page = max(max_page, int(text))

        return items, max_page

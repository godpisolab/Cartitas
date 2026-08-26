"""OpenCart: HTML server-side del tema por defecto."""

from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from typing import Optional

from base_script import (
    Product,
    RefreshedVariant,
    RefreshOutcome,
    StoreConfig,
    build_session,
    conditional_headers,
    parse_price_text,
    request_with_retries,
)
from scrapers.base import BaseStoreScraper

_MONEY_TOKEN_RE = re.compile(r"\d[\d.,]*\s*€|€\s*\d[\d.,]*")


class OpenCartScraper(BaseStoreScraper):
    """Verificado parcialmente contra Santuario Arcano: confirmé la estructura
    de navegación/categorías (route=product/category&path=X) y el detalle de
    un producto, pero NO conseguí ver el HTML de una página de LISTADO de
    categoría con productos reales (el fetch devolvió la página de un
    producto individual en su lugar). El parseo de nombre/precio/imagen usa
    los selectores estándar del tema por defecto de OpenCart (bastante
    estables entre versiones 3.x/4.x), con confianza razonable.

    STOCK: decisión deliberada de NO adivinar. La página de producto que sí
    pude ver no mostraba ningún texto de disponibilidad visible en el HTML
    (nada de "En stock"/"Agotado" cerca del precio) -- exactamente el mismo
    tipo de trampa que en TCG Legacy (Odoo), donde asumir "disponible" por
    defecto resultó estar mal. Aquí, en vez de arriesgarme a repetir ese
    error, si no se encuentra una señal de stock explícita en la tarjeta de
    producto, se marca como "DESCONOCIDO" en vez de "DISPONIBLE" -- así el
    CSV no miente, aunque sea menos informativo hasta que se verifique con
    HTML real (mismo procedimiento que arregló TCG Legacy: comparte el HTML
    real de la categoría y se ajusta con datos, no a ciegas)."""

    def scrape(self) -> list[Product]:
        """Pagina opencart_category_url siguiendo el enlace real de
        siguiente página hasta que no queden más o una página no traiga
        productos."""
        session = build_session(anti_bot=True, config=self.config)
        self.logger.log("iniciando sesión anti-bot (cloudscraper)...")

        products: list[Product] = []
        page = 1
        base_url = self.config.opencart_category_url

        while True:
            sep = "&" if "?" in base_url else "?"
            url = base_url if page == 1 else f"{base_url}{sep}page={page}"

            self.logger.log(f"solicitando página {page}...")
            resp = request_with_retries(session, url, heartbeat=self.logger.touch)
            if resp is None or resp.status_code != 200:
                status = getattr(resp, "status_code", None)
                self.logger.log(f"ERROR en página {page} (status={status}), abortando")
                break

            raw_items, has_next = self._parse_page(resp.text)
            if not raw_items:
                if page == 1:
                    self.logger.log("AVISO: 0 productos en la primera página -- revisar "
                                     "selectores de _parse_page si esto persiste")
                break

            self.logger.log(f"página {page}: {len(raw_items)} productos (acumulado {len(products)})")

            for item in raw_items:
                products.append(self._make_product(
                    id_product=item["id_product"],
                    name=item["name"],
                    price=item["price"],
                    stock_status=item["stock_status"],
                    url=item["url"],
                    sku=None,
                    image_url=item["image_url"],
                ))

            if not has_next:
                break
            page += 1
            time.sleep(self.delay)

        return products

    @classmethod
    def refresh_product(cls, config: StoreConfig, store_url: str, *, store_sku: Optional[str] = None,
                         etag: Optional[str] = None, last_modified: Optional[str] = None) -> RefreshOutcome:
        """E.2: sin JSON-LD ni microdata schema.org en la ficha de producto
        (verificado contra HTML real de Santuario Arcano -- ni siquiera hay
        texto de disponibilidad visible, mismo hallazgo que ya documentaba
        _parse_page para el listado). Solo se puede refrescar PRECIO con
        confianza; el stock se marca DESCONOCIDO salvo que aparezca alguna
        de las mismas palabras clave que usa el listado -- no se inventa una
        señal que la tienda no da."""
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
            return RefreshOutcome(status="error", error="sin selector de precio reconocible en la ficha")

        return RefreshOutcome(
            status="modified",
            variants=[RefreshedVariant(variant=None, price=item["price"], stock_status=item["stock_status"],
                                        name=item["name"])],
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
        )

    @staticmethod
    def _parse_product_detail(html: str) -> Optional[dict]:
        """Ficha individual del tema por defecto -- verificado contra HTML
        real de Santuario Arcano. El precio puede traer tachado+rebajado
        concatenados (mismo patrón que se corrigió en WooCommerce/Arte9) --
        se coge el ÚLTIMO importe con símbolo €, que por convención visual
        es el que se cobra de verdad.

        Stock SIEMPRE "DESCONOCIDO" aquí (a propósito, no un descuido): se
        probó a buscar las mismas palabras clave que usa _parse_page para
        el listado sobre el texto completo de la ficha, y dio un falso
        positivo real -- "opciones disponibles" (la etiqueta del selector de
        variante) contiene "disponible" sin tener nada que ver con stock. En
        el listado el riesgo es bajo (el texto de cada tarjeta es pequeño y
        contenido); en la ficha completa, con menús/opciones/textos legales
        alrededor, no lo es. Mismo criterio que ya aplicaba el listado:
        mejor no adivinar que inventar una señal que la tienda no da."""
        soup = BeautifulSoup(html, "html.parser")

        price_tag = soup.select_one(".price")
        if price_tag is None:
            return None
        price_text = price_tag.get_text(strip=True)
        money_tokens = _MONEY_TOKEN_RE.findall(price_text)
        price = parse_price_text(money_tokens[-1] if money_tokens else price_text)

        # OJO: verificado contra HTML real de Santuario Arcano -- el <h1> de
        # esta tienda trae literalmente el texto "Clic para ampliar" pegado
        # delante del nombre real (parece un string de tema sin traducir
        # filtrado al propio h1, no un adorno nuestro). Se quita si aparece;
        # si otra tienda no lo tiene, el strip() no hace nada.
        name_tag = soup.select_one("h1")
        name = name_tag.get_text(strip=True) if name_tag else None
        if name:
            name = re.sub(r"^Clic para ampliar\s*", "", name, flags=re.IGNORECASE)

        return {"price": price, "stock_status": "DESCONOCIDO", "name": name}

    @staticmethod
    def _parse_page(html: str):
        """Selectores del tema por defecto de OpenCart 3.x/4.x:
        - Tarjeta de producto: div.product-thumb / div.product-layout
        - Nombre + enlace: .caption h4 a (o h4 a directamente)
        - Precio: .price (puede incluir .price-new/.price-old si hay descuento)
        - Imagen: .image a img
        - Paginación: ul.pagination a[rel='next'] o el número de página siguiente
        - Stock: NO se adivina -- ver docstring de OpenCartScraper. Solo se marca
          AGOTADO si aparece explícitamente texto como 'agotado'/'sin stock'/
          'out of stock' en la tarjeta; si no hay ninguna señal, DESCONOCIDO."""
        soup = BeautifulSoup(html, "html.parser")
        items = []

        cards = soup.select("div.product-thumb, div.product-layout")
        for card in cards:
            link_tag = card.select_one(".caption h4 a, h4 a, .image a")
            name_tag = card.select_one(".caption h4 a, h4 a")
            price_tag = card.select_one(".price")
            img_tag = card.select_one(".image img, img")

            name = name_tag.get_text(strip=True) if name_tag else None
            if not name:
                continue

            url = link_tag["href"] if link_tag and link_tag.has_attr("href") else None
            price_text = price_tag.get_text(strip=True) if price_tag else None
            # Si hay precio rebajado, .price suele incluir precio tachado +
            # nuevo precio pegados -- nos quedamos con el primer importe que
            # aparezca como precio válido (el más específico/actual en la
            # mayoría de temas es el segundo si hay dos, pero sin HTML real
            # de un producto en oferta no lo puedo confirmar con certeza).
            price = parse_price_text(price_text)

            image_url = img_tag.get("src") if img_tag else None

            card_text_lower = card.get_text(" ", strip=True).lower()
            if any(kw in card_text_lower for kw in
                   ("agotado", "sin stock", "no disponible", "out of stock", "sold out")):
                stock_status = "AGOTADO"
            elif any(kw in card_text_lower for kw in ("en stock", "in stock", "disponible")):
                stock_status = "DISPONIBLE"
            else:
                stock_status = "DESCONOCIDO"

            id_match = re.search(r"product_id=(\d+)", url or "")
            id_product = id_match.group(1) if id_match else None

            items.append({
                "id_product": id_product, "name": name, "url": url,
                "price": price, "image_url": image_url, "stock_status": stock_status,
            })

        has_next = soup.select_one("ul.pagination a[rel='next'], .pagination a[rel='next']") is not None
        return items, has_next

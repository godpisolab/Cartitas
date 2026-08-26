"""WooCommerce: Store API JSON (filtrada por categoría) con fallback a HTML."""

from __future__ import annotations

import time
from typing import Optional

from bs4 import BeautifulSoup

from base_script import (
    Product,
    build_session,
    parse_price_minor_unit,
    parse_price_text,
    request_with_retries,
)
from scrapers.base import BaseStoreScraper


class WooCommerceScraper(BaseStoreScraper):
    """Store API JSON (rápida, filtrable por categoría) con fallback a HTML
    del tema Storefront si esa API no está disponible o no da resultados
    fiables. Es la plataforma con más tiendas y más variantes de scoping de
    todo el proyecto (ver los campos woocommerce_* de StoreConfig)."""

    STORE_API_ENDPOINTS = ("/wp-json/wc/store/v1/products", "/wp-json/wc/store/products")

    def scrape(self) -> list[Product]:
        """Intenta la Store API primero; si no da productos (ni disponible
        ni con resultados), cae al fallback HTML. Se queda con lo que
        encuentre el fallback si la API vino vacía, en vez de aceptar ese
        vacío como definitivo (ver comentario de más abajo)."""
        products = self._scrape_via_api()
        if products:
            self.logger.log("Store API OK")
            return products

        # products == [] (API respondió pero sin productos) o None (API no
        # disponible) -- en ambos casos se prueba el fallback HTML si hay
        # ruta configurada. Verificado en Arte9 (2026-08-25): el endpoint
        # legacy /wp-json/wc/store/products respondió 200 con JSON válido
        # pero 0 productos para la categoría -- probablemente no soporta bien
        # el filtro `category`, no que la categoría esté realmente vacía (el
        # fallback HTML encontró 50 productos reales en esa misma categoría).
        # Aceptar un [] de la API como definitivo, sin cruzarlo contra el
        # fallback, arriesga reportar "sin stock" cuando en realidad es un
        # fallo silencioso del filtro de la API.
        if products is None:
            self.logger.log("Store API no disponible, usando fallback HTML")
        else:
            self.logger.log("Store API respondió sin productos -- probando fallback HTML "
                             "por si el filtro de categoría de la API no es fiable")

        if not self.config.woocommerce_fallback_paths:
            self.logger.log("Sin woocommerce_fallback_paths configuradas, no se puede hacer fallback")
            return products or []

        html_products = self._scrape_via_html()
        return html_products or (products or [])

    # -- Store API ----------------------------------------------------------

    def _scrape_via_api(self) -> Optional[list[Product]]:
        """Prueba los dos nombres de endpoint conocidos de la Store API (el
        moderno /v1/ y el legacy) hasta que uno responda JSON válido.
        Devuelve None si NINGUNO respondió (API no disponible de verdad),
        o la lista de productos (posiblemente vacía) del primero que sí
        respondió -- ver scrape() para por qué [] no se trata igual que None."""
        session = build_session(anti_bot=False)
        session.headers["Accept"] = "application/json"
        category_slug = self.config.woocommerce_category_slug
        name_must_include = self.config.woocommerce_name_must_include

        for endpoint in self.STORE_API_ENDPOINTS:
            if category_slug:
                scope_msg = f"categoría: {category_slug}"
            elif name_must_include:
                scope_msg = f"por nombre, debe incluir: {name_must_include}"
            else:
                scope_msg = "SIN scoping -- confiando en el fallback si esto trae demasiado"
            self.logger.log(f"probando endpoint {endpoint} ({scope_msg})...")

            products, endpoint_ok = self._paginate_api(session, endpoint, category_slug, name_must_include)
            if endpoint_ok:
                return products

        return None

    def _paginate_api(self, session, endpoint, category_slug,
                       name_must_include: tuple[str, ...] = ()) -> tuple[list[Product], bool]:
        """Pagina un endpoint concreto de la Store API hasta agotar
        X-WP-TotalPages (o una página con menos de per_page resultados).
        Devuelve (productos, endpoint_ok): endpoint_ok=False significa "este
        endpoint concreto no sirve, prueba el siguiente" (404 o respuesta no
        JSON), no "no hay productos" -- esa distinción es la que usa
        _scrape_via_api para decidir si seguir probando endpoints."""
        products: list[Product] = []
        page = 1
        per_page = 100
        total_seen = 0
        total_filtered_out = 0
        has_scope = bool(category_slug or name_must_include)

        while True:
            url = f"{self.config.domain}{endpoint}"
            params = {"per_page": per_page, "page": page}
            if category_slug:
                params["category"] = category_slug

            self.logger.log(f"solicitando {endpoint} página {page}...")
            resp = request_with_retries(session, url, params=params, heartbeat=self.logger.touch)

            if resp is None or resp.status_code == 404:
                return products, False
            if resp.status_code != 200:
                self.logger.log(f"{endpoint} devolvió {resp.status_code}, probando otra ruta")
                return products, False
            try:
                data = resp.json()
            except ValueError:
                return products, False  # Store API desactivada: la respuesta no es JSON

            if not isinstance(data, list) or not data:
                break

            total_seen += len(data)
            self.logger.log(f"{endpoint} página {page}: {len(data)} productos recibidos "
                             f"(acumulado {len(products)})")

            for item in data:
                if not self._product_in_scope(item, category_slug, name_must_include):
                    total_filtered_out += 1
                    continue
                products.append(self._product_from_api_item(item))

            total_pages = resp.headers.get("X-WP-TotalPages")
            if total_pages and page >= int(total_pages):
                break
            if len(data) < per_page:
                break
            if not has_scope and page >= 20 and not products:
                self.logger.log("AVISO: 20 páginas sin scoping configurado y sin resultados "
                                 "relevantes -- abortando para no recorrer todo el catálogo a lo tonto.")
                break

            page += 1
            time.sleep(self.delay)

        if has_scope and (total_seen or total_filtered_out):
            self.logger.log(f"{endpoint}: {total_seen} vistos, {total_filtered_out} descartados "
                             f"por scoping, {len(products)} válidos")

        return products, True

    @staticmethod
    def _product_in_scope(item: dict, category_slug: Optional[str],
                           name_must_include: tuple[str, ...] = ()) -> bool:
        """Confirma que el producto pertenece a la categoría esperada. Dos
        mecanismos posibles, combinados con AND -- si se configuran ambos,
        el producto debe cumplir los dos a la vez, no basta con uno solo.
        (Un producto que solo cumple UNO de los dos criterios configurados
        NO está en scope: si configuras category_slug="jc-tcg" +
        name_must_include=("one piece",), lo que quieres es la intersección
        -- "de la categoría de TCG Y que además sea One Piece" -- no la
        unión, que colaría los otros ~270 productos de esa categoría.)

        1) Por SLUG EXACTO de categoría (nunca por nombre de categoría) --
           el método preferido, ver nota histórica sobre Arte9/merchandising.
        2) Por palabras obligatorias en el NOMBRE DEL PRODUCTO -- para
           tiendas sin categoría estándar fiable (El Encuentro), o para
           acotar dentro de una categoría amplia que sí existe pero mezcla
           varios juegos (inGenio BCN: categoría "jc-tcg" = todos los TCG).
           Menos riguroso que el slug: solo seguro si el catálogo no tiene
           categorías ambiguas que compartan palabras con el nombre del juego."""
        if not category_slug and not name_must_include:
            return True  # sin scoping configurado (no debería pasar, StoreConfig lo valida)

        if category_slug:
            slug_lower = category_slug.lower()
            category_ok = any(
                (cat.get("slug") or "").lower() == slug_lower
                for cat in (item.get("categories") or [])
            )
            if not category_ok:
                return False

        if name_must_include:
            name_lower = (item.get("name") or "").lower()
            if not all(kw.lower() in name_lower for kw in name_must_include):
                return False

        return True

    def _product_from_api_item(self, item: dict) -> Product:
        """Convierte un item crudo de la Store API a Product. El precio viene
        en la unidad mínima de la moneda (céntimos), de ahí
        parse_price_minor_unit; la disponibilidad prioriza is_in_stock
        (booleano directo) y cae a stock_status=="instock" si no está."""
        prices = item.get("prices") or {}
        minor_unit = int(prices.get("currency_minor_unit", 2) or 2)
        images = item.get("images") or []
        first_image = images[0] if images and isinstance(images[0], dict) else None

        disponible = item.get("is_in_stock")
        if disponible is None:
            disponible = item.get("stock_status") == "instock"

        return self._make_product(
            id_product=item.get("id"),
            name=item.get("name"),
            price=parse_price_minor_unit(prices.get("price"), minor_unit),
            stock_status="DISPONIBLE" if disponible else "AGOTADO",
            url=item.get("permalink"),
            sku=item.get("sku"),
            image_url=first_image.get("src") if first_image else None,
        )

    # -- Fallback HTML --------------------------------------------------------

    def _scrape_via_html(self) -> list[Product]:
        """Recorre cada woocommerce_fallback_paths con el tema Storefront
        genérico (ver _parse_html_page), siguiendo el enlace real de
        "siguiente página" en vez de construir la URL a mano -- distintas
        tiendas paginan de forma distinta. Aplica name_must_include como
        post-filtro si está configurado (misma lógica que en la Store API)."""
        session = build_session(anti_bot=True)
        self.logger.log("iniciando sesión anti-bot (cloudscraper) para el fallback HTML...")
        products: list[Product] = []
        name_must_include = self.config.woocommerce_name_must_include

        for path in self.config.woocommerce_fallback_paths:
            path_clean = path.strip("/")
            url = f"{self.config.domain}/{path_clean}/"
            page = 1

            while url:
                self.logger.log(f"solicitando {path_clean} página {page}...")
                resp = request_with_retries(session, url, heartbeat=self.logger.touch)
                if resp is None or resp.status_code != 200:
                    self.logger.log(f"ERROR en {url}, deteniendo esta categoría")
                    break

                raw_items, next_url = self._parse_html_page(resp.text)
                if not raw_items:
                    break

                # Si el fallback apunta a una categoría amplia (p.ej. "TCG" general,
                # mezclando varios juegos -- caso de El Encuentro / inGenio BCN),
                # se filtra aquí por nombre, igual que en la Store API.
                if name_must_include:
                    before = len(raw_items)
                    raw_items = [
                        it for it in raw_items
                        if all(kw.lower() in (it["name"] or "").lower() for kw in name_must_include)
                    ]
                    self.logger.log(f"{path_clean} página {page}: {before} productos recibidos, "
                                     f"{len(raw_items)} coinciden con {name_must_include}")
                else:
                    self.logger.log(f"{path_clean} página {page}: {len(raw_items)} productos "
                                     f"(acumulado {len(products)})")

                for item in raw_items:
                    products.append(self._make_product(
                        id_product=None,
                        name=item["name"],
                        price=item["price"],
                        stock_status=item["stock_status"],
                        url=item["url"],
                        sku=None,
                        image_url=item["image_url"],
                    ))

                if not next_url:
                    break
                # Seguimos el enlace "siguiente" REAL de la propia página en vez de
                # construir la URL a mano -- distintas tiendas WooCommerce paginan
                # de forma distinta (/page/N/ vs ?product-page=N vs otros esquemas
                # de theme/plugin), y adivinar el patrón falla en silencio.
                url = next_url if next_url.startswith("http") else f"{self.config.domain}{next_url}"
                page += 1
                time.sleep(self.delay)

        return products

    @staticmethod
    def _parse_html_page(html: str):
        """Parser genérico para el tema Storefront por defecto de WooCommerce. Si
        una tienda usa un tema muy personalizado (p.ej. Madara, visto en Arte9),
        esto puede no encontrar nada -- en ese caso, prioriza conseguir que la
        Store API funcione (con category_slug) para esa tienda, en vez de
        perseguir selectores CSS de tema en tema.

        Devuelve (items, next_url): next_url es el href REAL del enlace de
        siguiente página (o None si no hay más), no una URL construida a mano
        -- necesario porque el esquema de paginación varía entre temas/plugins
        (WordPress clásico usa /page/N/, algunos temas usan ?product-page=N,
        confirmado en inGenio BCN)."""
        soup = BeautifulSoup(html, "html.parser")
        items = []

        for item in soup.select("li.product, div.product"):
            link_tag = item.select_one("a.woocommerce-LoopProduct-link, a.product-link, h2 a, h3 a")
            name_tag = item.select_one(".woocommerce-loop-product__title, .product-title, h2, h3")
            price_tag = item.select_one(".price .amount, .price bdi, .price")
            img_tag = item.select_one("img")

            name = name_tag.get_text(strip=True) if name_tag else None
            if not name:
                continue

            items.append({
                "name": name,
                "url": link_tag["href"] if link_tag and link_tag.has_attr("href") else None,
                "price": parse_price_text(price_tag.get_text(strip=True)) if price_tag else None,
                "stock_status": "AGOTADO" if "outofstock" in " ".join(item.get("class", [])) else "DISPONIBLE",
                "image_url": (img_tag.get("src") or img_tag.get("data-src")) if img_tag else None,
            })

        next_link = soup.select_one("a.next.page-numbers")
        next_url = next_link["href"] if next_link and next_link.has_attr("href") else None
        return items, next_url

"""Modelo de dominio: dataclasses/enum puros, sin lógica ni imports internos
del proyecto -- la capa más baja (ver docs/estandares_organizacion_codigo.md,
sección 2). Todo lo demás (config, clasificación, HTTP, persistencia,
dispatcher, scrapers) depende de esto; esto no depende de nada de ellos.

Vive en shared/ (no en store_monitor/) porque tanto store_monitor/ como
api/ lo necesitan -- ver decisión de arquitectura sobre el acoplamiento
entre ambos servicios (patrón Shared Kernel de DDD)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional


class Platform(str, Enum):
    """Las plataformas de tienda que el dispatcher sabe scrapear -- cada una
    mapea a una clase en scrapers/ (ver scrapers.SCRAPER_CLASSES)."""

    SHOPIFY = "shopify"
    PRESTASHOP = "prestashop"
    WOOCOMMERCE = "woocommerce"
    ODOO = "odoo"
    OPENCART = "opencart"
    GENERIC_JSONLD = "generic_jsonld"


@dataclass
class StoreConfig:
    """Configuración de una tienda. Se autovalida en __post_init__: si falta
    un campo imprescindible para su plataforma, falla en el arranque del
    script con un mensaje claro, en vez de scrapear mal (o de más) en
    silencio. Esto es intencional -- es la corrección estructural de los
    bugs de scoping de Arte9/ZIAL: antes nada impedía omitir el filtro de
    categoría; ahora es un error de configuración explícito."""

    label: str
    domain: str
    platform: Platform

    # Shopify: slug de la colección (lo que va después de /collections/)
    shopify_collection: Optional[str] = None

    # PrestaShop: URL completa de la categoría (listado de productos)
    prestashop_category_url: Optional[str] = None

    # WooCommerce:
    #   woocommerce_category_slug -> slug DEL TÉRMINO de categoría (no la ruta
    #     completa). Se manda como filtro de servidor a la Store API y ADEMÁS
    #     se usa como post-filtro local (ver WooCommerceScraper._product_in_scope)
    #     por si el servidor lo ignora. Tiendas 100% mono-producto (todo su
    #     catálogo es relevante) pueden omitirlo.
    #   woocommerce_name_must_include -> mecanismo de scoping ALTERNATIVO para
    #     tiendas que NO exponen una categoría estándar de WooCommerce fiable
    #     (p.ej. El Encuentro, que filtra por una taxonomía custom de Elementor
    #     en vez de la categoría nativa). Todas estas subcadenas deben aparecer
    #     en el nombre del producto (case-insensitive) para considerarlo en
    #     scope. MENOS RIGUROSO que category_slug -- solo usar cuando no hay
    #     alternativa, y solo en tiendas donde el riesgo de falso positivo sea
    #     bajo (catálogo enfocado, sin categorías de merchandising que
    #     compartan palabras con el nombre del juego).
    #   woocommerce_fallback_paths -> rutas de categoría relativas (sin dominio,
    #     sin barra inicial) usadas si la Store API no está disponible.
    woocommerce_category_slug: Optional[str] = None
    woocommerce_name_must_include: tuple[str, ...] = field(default_factory=tuple)
    woocommerce_fallback_paths: tuple[str, ...] = field(default_factory=tuple)

    # Odoo: URL completa de la categoría (listado de productos), estilo
    # /shop/category/<slug>-<id>
    odoo_category_url: Optional[str] = None

    # OpenCart: URL completa de la categoría (listado de productos), estilo
    # index.php?route=product/category&path=<id> o <id_padre>_<id_hijo>
    opencart_category_url: Optional[str] = None

    # Genérico JSON-LD: para tiendas con CMS a medida (ninguna de las 5
    # plataformas de arriba) que exponen JSON-LD schema.org de tipo Product en
    # sus páginas de producto. GenericJsonLdScraper recolecta URLs de producto
    # visitando jsonld_listing_urls (una o varias -- puede ser una categoría o
    # una URL de búsqueda interna) y siguiendo TODOS los enlaces de paginación
    # reales que encuentre (no construye URLs de página a mano: cada tienda
    # pagina de forma distinta). jsonld_product_link_selector es el selector
    # CSS que localiza los <a href> a páginas de producto dentro de esas
    # páginas de listado.
    jsonld_listing_urls: tuple[str, ...] = field(default_factory=tuple)
    jsonld_product_link_selector: Optional[str] = None

    # A.1: True SOLO para tiendas que confirmadamente bloquean
    # IDENTIFIABLE_USER_AGENT tras probarlo -- excepción documentada por
    # tienda (anotar el motivo al lado, como con woocommerce_name_must_include),
    # nunca el comportamiento por defecto.
    ua_exception: bool = False

    def __post_init__(self):
        """Normaliza domain y valida que la config trae lo imprescindible
        para su plataforma (ver docstring de la clase)."""
        self.domain = self.domain.rstrip("/")

        if self.platform == Platform.SHOPIFY and not self.shopify_collection:
            raise ValueError(f"{self.label}: falta 'shopify_collection'")

        if self.platform == Platform.PRESTASHOP and not self.prestashop_category_url:
            raise ValueError(f"{self.label}: falta 'prestashop_category_url'")

        if self.platform == Platform.ODOO and not self.odoo_category_url:
            raise ValueError(f"{self.label}: falta 'odoo_category_url'")

        if self.platform == Platform.OPENCART and not self.opencart_category_url:
            raise ValueError(f"{self.label}: falta 'opencart_category_url'")

        if self.platform == Platform.GENERIC_JSONLD:
            if not self.jsonld_listing_urls:
                raise ValueError(f"{self.label}: falta 'jsonld_listing_urls'")
            if not self.jsonld_product_link_selector:
                raise ValueError(f"{self.label}: falta 'jsonld_product_link_selector'")

        if self.platform == Platform.WOOCOMMERCE:
            has_scope = bool(self.woocommerce_category_slug or self.woocommerce_name_must_include)
            has_fetch_path = bool(self.woocommerce_category_slug or self.woocommerce_fallback_paths)

            if not has_scope:
                raise ValueError(
                    f"{self.label}: tienda WooCommerce sin 'woocommerce_category_slug' NI "
                    f"'woocommerce_name_must_include' -- sin uno de los dos no hay forma de "
                    f"acotar el catálogo, y el scraper acabaría trayéndose todo lo que vende "
                    f"la tienda (el bug original de Arte9/ZIAL)."
                )
            if not has_fetch_path:
                raise ValueError(
                    f"{self.label}: tienda WooCommerce sin 'woocommerce_category_slug' NI "
                    f"'woocommerce_fallback_paths' -- sin al menos uno de los dos no hay "
                    f"ruta de fallback HTML si la Store API falla. Si el scoping es solo por "
                    f"'woocommerce_name_must_include', añade igualmente una fallback_path "
                    f"(aunque sea una categoría amplia) para que el fallback HTML tenga "
                    f"algo que recorrer y filtrar por nombre."
                )


CSV_FIELDNAMES = [
    "store", "platform", "id_product", "name", "variant", "product_type",
    "main_set", "set_code", "language", "price", "stock_status", "url", "sku",
    "image_url",
]


@dataclass
class Product:
    """Fila normalizada, idéntica para las tres plataformas."""
    store: str
    platform: str
    id_product: Optional[str]
    name: Optional[str]
    variant: Optional[str]           # título de variante (idioma, grading...); None si N/A
    product_type: str
    main_set: Optional[str]
    set_code: Optional[str]
    language: Optional[str]
    price: Optional[float]
    stock_status: str
    url: Optional[str]
    sku: Optional[str]
    image_url: Optional[str]

    def to_dict(self) -> dict:
        """Convierte a dict plano -- lo que espera csv.DictWriter en write_products_csv."""
        return asdict(self)


@dataclass
class Classification:
    """Resultado de classify_product() (ver classify.py): a qué categoría/
    set/idioma pertenece un producto, deducido de su nombre (y opcionalmente
    del título de variante)."""

    product_type: str
    set_code: Optional[str]
    language: Optional[str]
    main_set: Optional[str]


@dataclass
class RefreshedVariant:
    """Precio/stock recién leídos de UNA variante de un producto. `variant`
    debe coincidir exactamente con el `raw_variant` ya guardado en
    store_product para que el llamador sepa qué fila actualizar -- en
    plataformas sin variantes (todas salvo Shopify) siempre es None.

    `name` es el nombre crudo tal como lo devuelve la plataforma -- todos
    los parsers ya lo extraen de todas formas (lo necesitan para construir
    el Product del barrido normal), así que llevarlo aquí es gratis. El
    refresco de calientes (E.2) lo ignora (el producto ya se conoce, reusa
    el raw_name guardado), pero el polling de sitemap (E.1) lo necesita:
    ahí el producto es nuevo, no hay raw_name previo del que partir."""
    variant: Optional[str]
    price: Optional[float]
    stock_status: str
    name: Optional[str] = None


@dataclass
class RefreshOutcome:
    """Resultado de BaseStoreScraper.refresh_product() (ver scrapers/base.py).

    status:
        "modified"      -- cambió (o es la primera vez que se comprueba):
                           `variants` trae el precio/stock actual de cada
                           variante conocida de esa URL.
        "not_modified"  -- 304: no ha cambiado desde la vez anterior, no hay
                           nada que reprocesar (solo se actualiza
                           last_checked_at, barato -- ver A.4).
        "error"         -- fallo de red o de parseo; no se toca la BBDD,
                           se reintentará en el siguiente ciclo.
        "not_supported" -- esta plataforma/producto no tiene forma fiable de
                           refrescarse individualmente (ver limitaciones por
                           plataforma en cada scrapers/*.py)."""
    status: str
    variants: list[RefreshedVariant] = field(default_factory=list)
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    error: Optional[str] = None

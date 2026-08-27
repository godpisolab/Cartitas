"""Interfaz común de scraper, compartida por todas las plataformas."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from shared.classify import classify_product
from shared.domain import Product, RefreshOutcome, StoreConfig
from http_client import DEFAULT_DELAY, StoreLogger


class BaseStoreScraper(ABC):
    """Cada subclase implementa scrape() y devuelve List[Product] ya
    normalizados y clasificados. El dispatcher no necesita saber nada de las
    diferencias entre plataformas."""

    def __init__(self, config: StoreConfig, logger: StoreLogger, delay: float = DEFAULT_DELAY):
        """delay es la pausa entre páginas/productos de ESTA tienda (no
        afecta a las demás, que corren en paralelo en su propio hilo)."""
        self.config = config
        self.logger = logger
        self.delay = delay

    @abstractmethod
    def scrape(self) -> list[Product]:
        """Recorre la tienda y devuelve todos los productos en scope, ya
        normalizados. Cada subclase decide cómo (JSON público, Store API,
        HTML, JSON-LD...) -- ver el módulo de cada plataforma en scrapers/."""
        ...

    @classmethod
    def refresh_product(cls, config: StoreConfig, store_url: str, *, store_sku: Optional[str] = None,
                         etag: Optional[str] = None, last_modified: Optional[str] = None) -> RefreshOutcome:
        """E.2/A.4: refresca UN producto ya conocido (no descubre nada
        nuevo, ver limitación en modelo-datos-app-tcg.md punto 4) sin
        recorrer la categoría entera. Método de clase (no de instancia): no
        hace falta un StoreLogger ni un `delay` de categoría para una sola
        petición puntual.

        Por defecto "not_supported" -- cada subclase que sepa refrescar un
        producto individual (ver scrapers/*.py) lo sobrescribe; las que no
        (ninguna en este proyecto por ahora) se quedan con este default en
        vez de fallar con un AttributeError."""
        return RefreshOutcome(status="not_supported", error=f"{cls.__name__} no soporta refresco individual todavía")

    def _make_product(self, *, id_product, name, price, stock_status, url, sku,
                       image_url, variant_title: Optional[str] = None,
                       type_hint: Optional[str] = None) -> Product:
        """Clasifica (classify_product) y empaqueta los campos crudos de un
        producto en un Product normalizado -- todas las subclases pasan por
        aquí en vez de construir Product a mano, así la clasificación nunca
        se les olvida.

        `type_hint` (2026-08-27): señal estructurada opcional adicional
        para el TIPO -- de momento solo ShopifyScraper la rellena, desde el
        campo `tags` nativo del comerciante. None para el resto (default),
        classify_product() ya sabe degradar sin ella."""
        c = classify_product(name, variant_title, type_hint)
        return Product(
            store=self.config.label,
            platform=self.config.platform.value,
            id_product=id_product,
            name=name,
            variant=variant_title,
            product_type=c.product_type,
            main_set=c.main_set,
            set_code=c.set_code,
            language=c.language,
            price=price,
            stock_status=stock_status,
            url=url,
            sku=sku,
            image_url=image_url,
            tags=type_hint,
        )

"""Schemas de `GET /products` -- ver docs/api-endpoints-v1.md, sección 1.
`ProductFilters` nunca cruza la frontera HTTP como JSON (el router lo
construye a partir de query params ya parseados), así que no necesita
`CamelModel`/alias -- solo `ProductSummary`, la respuesta real, lo necesita.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from models.product import ProductLanguage
from models.store_product import StockStatus
from schemas.common import CamelModel


@dataclass
class ProductFilters:
    q: str | None = None
    game: str | None = None
    category: str | None = None
    set_code: str | None = None
    language: ProductLanguage | None = None
    min_price: float | None = None
    max_price: float | None = None
    is_hot: bool | None = None
    page: int = 1
    limit: int = 20


class ProductSummary(CamelModel):
    id: int
    name_canonical: str
    game: str
    category: str
    set_code: str | None
    language: ProductLanguage | None
    min_price: float
    store_count: int
    any_in_stock: bool


class Listing(CamelModel):
    store_id: int
    store_name: str
    price: float | None
    stock_status: StockStatus
    url: str
    last_checked_at: datetime | None


class ProductDetail(CamelModel):
    id: int
    name_canonical: str
    game: str
    category: str
    set_code: str | None
    main_set: str | None
    language: ProductLanguage | None
    listings: list[Listing]


class PriceHistoryPoint(CamelModel):
    date: date
    min_price: float | None
    stock_status: StockStatus


class PriceHistorySeries(CamelModel):
    product_id: int
    store_id: int | None
    series: list[PriceHistoryPoint]


class ProductCreate(CamelModel):
    game_id: int
    category_id: int
    set_code: str | None = None
    main_set: str | None = None
    language: ProductLanguage | None = None
    name_canonical: str
    image_url: str | None = None
    is_hot: bool = False
    hot_until: date | None = None


class ProductPatch(CamelModel):
    """Edición parcial -- `categoryId`/`gameId`/`setCode` NO son editables
    en v1 (docs/api-endpoints-gestor.md sección 2): cambiar la categoría de
    un producto ya confirmado tendría efectos en cascada sobre el matching
    que no vale la pena resolver sin un caso real que lo pida."""
    name_canonical: str | None = None
    image_url: str | None = None
    is_hot: bool | None = None
    hot_until: date | None = None

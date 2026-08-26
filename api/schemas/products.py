"""Schemas de `GET /products` -- ver docs/api-endpoints-v1.md, sección 1.
`ProductFilters` nunca cruza la frontera HTTP como JSON (el router lo
construye a partir de query params ya parseados), así que no necesita
`CamelModel`/alias -- solo `ProductSummary`, la respuesta real, lo necesita.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.product import ProductLanguage
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

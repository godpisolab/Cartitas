"""Schemas de `GET /deals` -- docs/api-endpoints-v1.md sección 2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from schemas.common import CamelModel

# Ventana fija de comparación (ALERTA DE DISEÑO de api-endpoints-v1.md,
# sección 2): constante de backend, no query param en v1.
COMPARISON_WINDOW_DAYS = 7


@dataclass
class DealFilters:
    game: str | None = None
    category: str | None = None
    limit: int = 20


class DealItem(CamelModel):
    product_id: int
    name_canonical: str
    current_min_price: float
    previous_min_price: float
    drop_percentage: float
    compared_to: date
    store_name: str

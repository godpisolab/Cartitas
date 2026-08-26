"""Schemas de `GET /restock-events` -- docs/api-endpoints-v1.md sección 2.1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from schemas.common import CamelModel


@dataclass
class RestockEventFilters:
    game: str | None = None
    category: str | None = None
    hours: int = 24
    limit: int = 50


class RestockEventItem(CamelModel):
    product_id: int
    name_canonical: str
    store_name: str
    price: float | None
    detected_at: datetime

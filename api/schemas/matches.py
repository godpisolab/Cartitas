"""Schemas del panel de matching -- docs/api-endpoints-v1.md sección 6 y
docs/api-endpoints-gestor.md sección 1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from models.store_product import MatchStatus, StockStatus
from schemas.common import CamelModel

# Constante de backend, no query param en v1 (alerta #2 de
# api-endpoints-v1.md, sección de matching): un reject de hace más de esto
# vuelve a aparecer en la cola por defecto.
REVIEWED_HIDE_DAYS = 14

MatchStatusFilter = Literal["needsReview", "unmatched", "confirmed", "all"]

STATUS_FILTER_TO_ENUM = {
    "needsReview": MatchStatus.NEEDS_REVIEW,
    "unmatched": MatchStatus.UNMATCHED,
    "confirmed": MatchStatus.CONFIRMED,
}


@dataclass
class MatchFilters:
    status: MatchStatusFilter = "needsReview"
    store_id: int | None = None
    min_similarity: float | None = None
    max_similarity: float | None = None
    include_reviewed: bool = False
    page: int = 1
    limit: int = 50


class StoreRef(CamelModel):
    id: int
    name: str


class MatchCandidate(CamelModel):
    product_id: int
    name_canonical: str
    similarity: float


class MatchItem(CamelModel):
    store_product_id: int
    store: StoreRef
    raw_name: str
    raw_variant: str | None
    current_price: float | None
    stock_status: StockStatus
    match_status: MatchStatus
    reviewed_at: datetime | None
    reviewed_reason: str | None
    candidates: list[MatchCandidate]
    # Solo tienen valor real cuando match_status == confirmed (docs/
    # api-endpoints-gestor.md sección 1) -- se serializan como `null` en el
    # resto de casos en vez de omitirse del JSON: forma heterogénea por fila
    # sería más fiel al documento pero añade complejidad de serialización
    # sin beneficio real para un cliente que ya comprueba `if (productId)`.
    product_id: int | None = None
    match_confidence: float | None = None


class ConfirmBody(CamelModel):
    product_id: int


class RejectBody(CamelModel):
    mark_as: Literal["needsReview", "unmatched"]
    reason: str | None = None


class MissingCandidateItem(CamelModel):
    product_type: str
    main_set: str | None
    language: str | None
    store_count: int

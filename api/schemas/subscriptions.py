"""Schemas de suscripciones de restock -- docs/api-endpoints-v1.md sección
5. Sin `Idempotency-Key`: `estandares-implementacion-api.md` sección 7
decide (2026-08-27, contra este commit real) apoyarse en el `UNIQUE NULLS
NOT DISTINCT(productId, storeId, pushEndpoint)` del esquema -- un reintento
choca con esa constraint y devuelve `409`, que el frontend interpreta como
"ya estás suscrito" en vez de construir una tabla de `idempotency_record`
genérica sin un segundo caso de uso real que la justifique todavía."""

from __future__ import annotations

from datetime import datetime

from schemas.common import CamelModel


class PushKeys(CamelModel):
    p256dh: str
    auth: str


class SubscriptionCreate(CamelModel):
    product_id: int
    store_id: int | None = None
    push_endpoint: str
    push_keys: PushKeys


class SubscriptionSummary(CamelModel):
    id: int
    product_id: int
    store_id: int | None


class SubscriptionListItem(SubscriptionSummary):
    created_at: datetime

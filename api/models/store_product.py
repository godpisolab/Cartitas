"""Reflejo de la tabla `store_product` -- ver docstring de models/game.py.
`match_status_enum`/`stock_status_enum` ya existen como ENUM nativos de
Postgres; `create_type=False` en ambos por el mismo motivo que en
models/product.py."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlmodel import Field, SQLModel


class MatchStatus(str, Enum):
    UNMATCHED = "unmatched"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    NOT_APPLICABLE = "not_applicable"


class StockStatus(str, Enum):
    DISPONIBLE = "disponible"
    AGOTADO = "agotado"
    DESCONOCIDO = "desconocido"


class StoreProduct(SQLModel, table=True):
    __tablename__ = "store_product"

    id: int | None = Field(default=None, primary_key=True)
    store_id: int = Field(foreign_key="store.id")
    product_id: int | None = Field(default=None, foreign_key="product.id")
    match_confidence: float | None = None
    match_status: MatchStatus = Field(
        default=MatchStatus.UNMATCHED,
        sa_column=Column(
            PGEnum("unmatched", "needs_review", "confirmed", "not_applicable",
                   name="match_status_enum", create_type=False),
        ),
    )
    store_url: str
    store_sku: str | None = None
    raw_name: str
    raw_variant: str | None = None
    current_price: float | None = None
    stock_status: StockStatus = Field(
        default=StockStatus.DESCONOCIDO,
        sa_column=Column(
            PGEnum("disponible", "agotado", "desconocido", name="stock_status_enum", create_type=False),
        ),
    )
    last_etag: str | None = None
    last_modified_header: str | None = None
    last_checked_at: datetime | None = None
    # Independiente de match_status (API v1): cuándo un humano revisó esta
    # fila por última vez desde el panel -- matcher.run_matching() nunca la
    # toca. Ver GET /matches (routers/matches.py) para el uso del filtro.
    reviewed_at: datetime | None = None

"""Reflejo de la tabla `restock_subscription` -- ver docstring de
models/game.py."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class RestockSubscription(SQLModel, table=True):
    __tablename__ = "restock_subscription"

    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id")
    store_id: int | None = Field(default=None, foreign_key="store.id")
    push_endpoint: str
    push_keys: dict = Field(sa_column=Column(JSONB))
    # NOT NULL DEFAULT now() en el esquema -- ver comentario equivalente en
    # models/product.py sobre por qué esto necesita default_factory, no None.
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

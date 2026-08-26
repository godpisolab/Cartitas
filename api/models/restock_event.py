"""Reflejo de la tabla `restock_event` -- ver docstring de models/game.py."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class RestockEvent(SQLModel, table=True):
    __tablename__ = "restock_event"

    id: int | None = Field(default=None, primary_key=True)
    store_product_id: int = Field(foreign_key="store_product.id")
    product_id: int = Field(foreign_key="product.id")
    # NOT NULL DEFAULT now() en el esquema -- ver comentario equivalente en
    # models/product.py. api/ hoy solo LEE esta tabla (la escribe
    # restock_notifier.py vía SQL crudo), pero se corrige igual por si
    # algún día se inserta un RestockEvent vía este modelo.
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    subscribers_notified: int = 0

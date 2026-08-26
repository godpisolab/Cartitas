"""Reflejo de la tabla `price_history` -- ver docstring de models/game.py."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlmodel import Field, SQLModel

from models.store_product import StockStatus


class PriceHistory(SQLModel, table=True):
    __tablename__ = "price_history"

    id: int | None = Field(default=None, primary_key=True)
    store_product_id: int = Field(foreign_key="store_product.id")
    price: float | None = None
    stock_status: StockStatus = Field(
        sa_column=Column(
            PGEnum("disponible", "agotado", "desconocido", name="stock_status_enum", create_type=False),
        ),
    )
    scraped_date: date

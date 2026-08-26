"""Reflejo de la tabla `product` (catálogo canónico) -- ver docstring de
models/game.py. `product_language` ya existe como ENUM nativo de Postgres
(creado por schema-postgresql-app-tcg.sql); `create_type=False` evita que
SQLAlchemy intente crearlo de nuevo al levantar la app."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlmodel import Field, SQLModel


class ProductLanguage(str, Enum):
    EN = "EN"
    JP = "JP"
    ES = "ES"


class Product(SQLModel, table=True):
    __tablename__ = "product"

    id: int | None = Field(default=None, primary_key=True)
    game_id: int = Field(foreign_key="game.id")
    category_id: int = Field(foreign_key="category.id")
    set_code: str | None = None
    main_set: str | None = None
    language: ProductLanguage | None = Field(
        default=None,
        sa_column=Column(PGEnum("EN", "JP", "ES", name="product_language", create_type=False)),
    )
    name_canonical: str
    image_url: str | None = None
    is_hot: bool = False
    hot_until: date | None = None
    created_at: datetime | None = None

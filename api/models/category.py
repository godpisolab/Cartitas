"""Reflejo de la tabla `category` (jerárquica: parent_category_id) -- ver
docstring de models/game.py sobre la relación entre este fichero y el
esquema SQL."""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class Category(SQLModel, table=True):
    __tablename__ = "category"

    id: int | None = Field(default=None, primary_key=True)
    parent_category_id: int | None = Field(default=None, foreign_key="category.id")
    name: str
    slug: str

"""Schemas de `GET /games` y `GET /categories` -- docs/api-endpoints-v1.md
sección 4."""

from __future__ import annotations

from schemas.common import CamelModel


class GameItem(CamelModel):
    id: int
    name: str
    slug: str


class CategoryNode(CamelModel):
    id: int
    name: str
    slug: str
    children: list["CategoryNode"] = []

"""Reflejo de la tabla `game` (schema-postgresql-app-tcg.sql) -- sin lógica,
solo columnas. `models/` es la fuente de verdad de "cómo se guarda"; el
esquema SQL sigue siendo la fuente de verdad del propio esquema (ver
docs/estandares-implementacion-api.md, sección 3): si el `.sql` cambia,
esta clase se actualiza a mano para reflejarlo."""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class Game(SQLModel, table=True):
    __tablename__ = "game"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    slug: str

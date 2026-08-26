"""Base común de todo schema de respuesta: `camelCase` en el JSON
(estandares-api-app-tcg.md sección 5), decidido en un único sitio en vez de
renombrar campos a mano en cada router -- y el envelope de paginación
compartido por todo listado."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PageMeta(CamelModel):
    page: int
    limit: int
    total: int


class Page(CamelModel, Generic[T]):
    data: list[T]
    meta: PageMeta

"""Schemas de `GET /stores`, `GET /stores/{id}`, `PATCH /stores/{id}` --
docs/api-endpoints-v1.md sección 3 y docs/api-endpoints-gestor.md sección 3."""

from __future__ import annotations

from datetime import datetime

from models.store import StorePlatform
from schemas.common import CamelModel


class StoreSummary(CamelModel):
    id: int
    name: str
    website_url: str
    platform: StorePlatform
    active: bool


class StoreDetail(StoreSummary):
    # Ausente hasta ahora pese a que PATCH sí lo acepta -- hueco real
    # encontrado al construir el formulario de edición del panel (no se
    # puede prellenar un campo que la ficha nunca devuelve), docs/
    # plan-cierre-panel-gestor.md sección 1.5.
    sitemap_url: str | None
    last_scraped_at: datetime | None
    crawl_delay_seconds: int | None
    disallowed: bool
    consecutive_failures: int
    backoff_until: datetime | None


class StorePatch(CamelModel):
    """Todos los campos opcionales -- PATCH es edición parcial. `active` se
    cablea de verdad en dispatcher.run_all_stores() (2026-08-27, ver
    store_monitor/README.md): desactivar una tienda aquí SÍ la excluye del
    próximo barrido."""
    sitemap_url: str | None = None
    active: bool | None = None

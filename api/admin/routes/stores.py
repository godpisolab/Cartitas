"""Rutas HTML del panel de tiendas -- listado con columna de salud, detalle
+ edición de `sitemapUrl`/`active`. Llama a `services/stores.py`
DIRECTAMENTE, mismo patrón que el resto de `admin/routes/`
(docs/plan-cierre-panel-gestor.md sección 1.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

import services.jobs as jobs_service
import services.stores as stores_service
from admin.routes.jobs import _error_partial
from admin.templates_env import templates
from db import get_session
from errors import ApiError
from schemas.stores import StorePatch

router = APIRouter()


@router.get("/stores")
def list_stores(request: Request, session: Session = Depends(get_session)):
    items = stores_service.list_stores_detailed(session)
    return templates.TemplateResponse(request, "stores/list.html", {"items": items})


@router.get("/stores/{store_id}")
def store_detail(request: Request, store_id: int, session: Session = Depends(get_session)):
    store = stores_service.get_store(session, store_id)
    return templates.TemplateResponse(request, "stores/detail.html", {"store": store})


@router.post("/stores/{store_id}")
def update_store(
    request: Request,
    store_id: int,
    sitemap_url: str = Form(""),
    active: bool = Form(False),
    session: Session = Depends(get_session),
):
    stores_service.patch_store(session, store_id, StorePatch(sitemap_url=sitemap_url or None, active=active))
    return RedirectResponse(f"/admin/stores/{store_id}", status_code=303)


@router.post("/stores/{store_id}/scrape")
def trigger_store_scrape(
    request: Request,
    store_id: int,
    persist: bool = Form(False),
    session: Session = Depends(get_session),
):
    """Disparo manual de UN scrape puntual (docs/propuestas/
    propuesta-scraping-manual-panel.md punto 3, ampliado 2026-08-29 con la
    casilla "Guardar resultados") -- vía el servicio HTTP interno de
    store_monitor/, nunca importando dispatcher.query_store() aquí
    directamente. `store.name` es el `label` que usa config.STORES
    (sync_stores lo escribe tal cual, ver store_monitor/persistence.py).

    persist=False (checkbox sin marcar, por defecto): solo diagnóstico --
    nada se escribe en store_product/price_history, pero el resultado se ve
    igual en el fragmento de abajo (jobs/_run.html) una vez termina."""
    store = stores_service.get_store(session, store_id)
    try:
        run_id = jobs_service.trigger_store_scrape(store.name, persist=persist)
        run = jobs_service.get_run(run_id)
    except ApiError as e:
        return _error_partial(request, e)
    return templates.TemplateResponse(request, "jobs/_run.html", {"run": run})

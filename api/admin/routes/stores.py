"""Rutas HTML del panel de tiendas -- listado con columna de salud, detalle
+ edición de `sitemapUrl`/`active`. Llama a `services/stores.py`
DIRECTAMENTE, mismo patrón que el resto de `admin/routes/`
(docs/plan-cierre-panel-gestor.md sección 1.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

import services.stores as stores_service
from admin.templates_env import templates
from db import get_session
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

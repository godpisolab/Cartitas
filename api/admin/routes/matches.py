"""Rutas HTML del panel de matching -- llaman a `services/matches.py`
DIRECTAMENTE (mismo proceso Python, sin HTTP intermedio ni API key que
filtrar), igual función que usa `routers/matches.py` pero devolviendo
fragmentos htmx en vez de JSON (docs/estandares-implementacion-frontend.md
sección 2.3). La auth (HTTP Basic) se aplica una vez a nivel de router en
main.py, no aquí."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from sqlmodel import Session

import services.matches as matches_service
from admin.templates_env import templates
from db import get_session
from schemas.matches import ConfirmBody, MatchFilters, MatchStatusFilter, RejectBody

router = APIRouter()


@router.get("/matches")
def list_matches(
    request: Request,
    status: MatchStatusFilter = Query("needsReview"),
    session: Session = Depends(get_session),
):
    page = matches_service.list_matches(session, MatchFilters(status=status))
    return templates.TemplateResponse(request, "matches/list.html", {"items": page.data, "status": status})


@router.post("/matches/{store_product_id}/confirm")
def confirm_match(
    store_product_id: int,
    request: Request,
    product_id: int = Form(...),
    session: Session = Depends(get_session),
):
    item = matches_service.confirm_match(session, store_product_id, ConfirmBody(product_id=product_id))
    return templates.TemplateResponse(request, "matches/_row.html", {"item": item})


@router.post("/matches/{store_product_id}/reject")
def reject_match(
    store_product_id: int,
    request: Request,
    mark_as: str = Form(...),
    reason: str | None = Form(None),
    session: Session = Depends(get_session),
):
    item = matches_service.reject_match(session, store_product_id, RejectBody(mark_as=mark_as, reason=reason))
    return templates.TemplateResponse(request, "matches/_row.html", {"item": item})


@router.post("/matches/{store_product_id}/reopen")
def reopen_match(
    store_product_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    item = matches_service.reopen_match(session, store_product_id)
    return templates.TemplateResponse(request, "matches/_row.html", {"item": item})

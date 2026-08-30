"""Rutas HTML del panel de matching -- llaman a `services/matches.py`
DIRECTAMENTE (mismo proceso Python, sin HTTP intermedio ni API key que
filtrar), igual función que usa `routers/matches.py` pero devolviendo
fragmentos htmx en vez de JSON (docs/estandares-implementacion-frontend.md
sección 2.3). La auth (HTTP Basic) se aplica una vez a nivel de router en
main.py, no aquí."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from sqlmodel import Session

import services.jobs as jobs_service
import services.matches as matches_service
from admin.templates_env import templates
from db import get_session
from errors import ApiError
from schemas.matches import ConfirmBody, MatchFilters, MatchStatusFilter, RejectBody

router = APIRouter()


def _list_response(
    request: Request, session: Session, status: MatchStatusFilter, page: int,
    message: str | None = None, include_catalog_gaps: bool = False,
):
    result = matches_service.list_matches(
        session, MatchFilters(status=status, page=page, include_catalog_gaps=include_catalog_gaps),
    )
    return templates.TemplateResponse(
        request, "matches/list.html",
        {
            "items": result.data, "status": status, "meta": result.meta, "message": message,
            "include_catalog_gaps": include_catalog_gaps,
        },
    )


def _wrapper_response(
    request: Request, session: Session, status: MatchStatusFilter, page: int,
    message: str | None = None, error: str | None = None, include_catalog_gaps: bool = False,
):
    """Como `_list_response`, pero solo el fragmento #matches-wrapper --
    respuesta a una petición htmx (hx-swap="outerHTML" sobre ese id), nunca
    la página completa con `base.html`."""
    result = matches_service.list_matches(
        session, MatchFilters(status=status, page=page, include_catalog_gaps=include_catalog_gaps),
    )
    return templates.TemplateResponse(
        request, "matches/_wrapper.html",
        {
            "items": result.data, "status": status, "meta": result.meta, "message": message, "error": error,
            "include_catalog_gaps": include_catalog_gaps,
        },
    )


@router.get("/matches")
def list_matches(
    request: Request,
    status: MatchStatusFilter = Query("needsReview"),
    page: int = Query(1, ge=1),
    includeCatalogGaps: bool = Query(False),
    session: Session = Depends(get_session),
):
    # Antes de esto, el panel siempre pedía page=1 (limit=50 por defecto de
    # MatchFilters) sin exponer ?page= ni pasar meta a la plantilla -- con
    # más de 50 filas en un estado, el resto era invisible desde el
    # navegador sin ninguna forma de llegar a ellas (encontrado en vivo con
    # 187 needs_review reales, 2026-08-28).
    return _list_response(request, session, status, page, include_catalog_gaps=includeCatalogGaps)


@router.post("/matches/run-matching")
def run_matching(
    request: Request,
    status: MatchStatusFilter = Query("needsReview"),
    session: Session = Depends(get_session),
):
    """Relanza el matcher (vía store_monitor/jobs_api.py, ver services/jobs.py)
    contra toda la cola no confirmada y refresca la tabla en el estado
    actualmente filtrado -- mismo patrón que admin/routes/jobs.py: un error
    del servicio interno se pinta como mensaje visible en vez de dejar que
    el handler global de ApiError devuelva un status de error, que htmx no
    intercambia por defecto (docs/propuestas/propuesta-scraping-manual-panel.md,
    bug real 2026-08-29 con el botón de scraping)."""
    try:
        counts = jobs_service.trigger_run_matching()
    except ApiError as e:
        return _wrapper_response(request, session, status, 1, error=e.detail or e.title)

    resumen = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "sin filas que reevaluar"
    return _wrapper_response(request, session, status, 1, message=f"Matching relanzado: {resumen}.")


@router.get("/missing-candidates")
def missing_candidates(
    request: Request,
    minStores: int = Query(2, ge=1),
    session: Session = Depends(get_session),
):
    items = matches_service.missing_candidates(session, minStores)
    return templates.TemplateResponse(request, "matches/missing_candidates.html", {"items": items})


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

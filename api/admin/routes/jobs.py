"""Rutas HTML del panel de jobs de scraping -- disparo manual (barrido
diario/calientes/sitemap) + historial de ejecuciones (docs/propuestas/
propuesta-scraping-manual-panel.md punto 3). Llama a services/jobs.py
(cliente HTTP hacia store_monitor/jobs_api.py) -- nunca importa nada de
store_monitor/ directamente, ver ese módulo para el porqué."""

from __future__ import annotations

from fastapi import APIRouter, Request

import services.jobs as jobs_service
from admin.templates_env import templates
from errors import ApiError

router = APIRouter()


def _error_partial(request: Request, exc: ApiError):
    """Errores del servicio interno (jobs_api.py caído, tienda desconocida...)
    se pintan como fragmento normal en vez de dejar que el handler global de
    ApiError los convierta en application/problem+json -- htmx NO hace swap
    de respuestas con status de error por defecto (config.responseHandling),
    así que un 502/404 aquí se vería exactamente como "el botón no hace
    nada" (síntoma real reportado, 2026-08-29: jobs_api.py no estaba
    arrancado). Se devuelve 200 a propósito -- el contenido ya deja claro
    que algo falló, y así el swap sí ocurre."""
    return templates.TemplateResponse(request, "jobs/_error.html", {"message": exc.detail or exc.title})


def _run_partial(request: Request, run_id: int):
    try:
        run = jobs_service.get_run(run_id)
    except ApiError as e:
        return _error_partial(request, e)
    return templates.TemplateResponse(request, "jobs/_run.html", {"run": run})


def _trigger(request: Request, trigger_fn):
    try:
        run_id = trigger_fn()
    except ApiError as e:
        return _error_partial(request, e)
    return _run_partial(request, run_id)


@router.get("/jobs")
def list_jobs(request: Request):
    try:
        runs = jobs_service.list_runs()
    except ApiError as e:
        return templates.TemplateResponse(request, "jobs/list.html", {"runs": [], "error": e.detail or e.title})
    return templates.TemplateResponse(request, "jobs/list.html", {"runs": runs})


@router.post("/jobs/daily-sweep")
def trigger_daily_sweep(request: Request):
    return _trigger(request, jobs_service.trigger_daily_sweep)


@router.post("/jobs/hot-refresh")
def trigger_hot_refresh(request: Request):
    return _trigger(request, jobs_service.trigger_hot_refresh)


@router.post("/jobs/sitemap-poll")
def trigger_sitemap_poll(request: Request):
    return _trigger(request, jobs_service.trigger_sitemap_poll)


@router.get("/jobs/runs/{run_id}")
def poll_run(request: Request, run_id: int):
    """Fragmento reconsultado por htmx cada 2s mientras el run está
    'running' (ver jobs/_run.html) -- deja de auto-refrescarse solo una vez
    que status pasa a 'completed'/'failed'."""
    return _run_partial(request, run_id)

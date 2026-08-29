"""Servicio HTTP interno mínimo para lanzar/consultar scrapes bajo demanda
desde el panel de gestor (docs/propuestas/propuesta-scraping-manual-panel.md
punto 3). Vive en `store_monitor/` -- no un import de Python desde `api/`
(que no tiene `cloudscraper`/`pybreaker`, ver docs/plan-cierre-panel-gestor.md
sección "aplazado explícitamente"), sino un proceso HTTP de verdad al que
`api/admin/routes/stores.py` llama por HTTP: aquí sí hace falta un proceso
vivo con estado (qué se está ejecutando ahora mismo), no solo funciones
puras -- justo el caso para el que HTTP entre servicios es la herramienta
correcta.

Cada POST /jobs/* crea la fila `scrape_run` y devuelve `{run_id}` de
inmediato (scheduler.launch_*, en un hilo de fondo) -- nunca bloquea la
petición hasta que el scrape termina, que puede tardar minutos.

Seguridad: pensado para escuchar SOLO en localhost o la red interna donde
vive `api/` -- no se expone públicamente. JOBS_API_TOKEN es opcional: solo
hace falta si `api/` y este servicio corren en hosts distintos (token
compartido por variable de entorno, nada más elaborado -- la red ya aísla
el caso normal de un único host).

Uso: uvicorn jobs_api:app --host 127.0.0.1 --port 8001
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException

import live_progress
import persistence
import run_results
import scheduler
from run_logging import log_path

JOBS_API_TOKEN = os.environ.get("JOBS_API_TOKEN")  # None = sin auth propia (la red ya aísla el servicio)

app = FastAPI(title="Cartitas -- jobs internos de scraping")


def _check_auth(authorization: str | None) -> None:
    if JOBS_API_TOKEN is None:
        return
    if authorization != f"Bearer {JOBS_API_TOKEN}":
        raise HTTPException(status_code=401, detail="token inválido o ausente")


def _run_or_404(run_id: int) -> dict:
    conn = persistence.get_connection()
    try:
        run = persistence.get_scrape_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404, detail=f"scrape_run {run_id} no encontrado")
    return run


@app.post("/jobs/daily-sweep")
def trigger_daily_sweep(authorization: str | None = Header(None)) -> dict:
    _check_auth(authorization)
    return {"run_id": scheduler.launch_daily_sweep()}


@app.post("/jobs/hot-refresh")
def trigger_hot_refresh(authorization: str | None = Header(None)) -> dict:
    _check_auth(authorization)
    return {"run_id": scheduler.launch_hot_refresh()}


@app.post("/jobs/sitemap-poll")
def trigger_sitemap_poll(authorization: str | None = Header(None)) -> dict:
    _check_auth(authorization)
    return {"run_id": scheduler.launch_sitemap_poll()}


@app.post("/jobs/store/{label}")
def trigger_single_store(label: str, persist: bool = False, authorization: str | None = Header(None)) -> dict:
    """persist=False (por defecto): solo diagnóstico, nada se escribe en
    store_product/price_history -- ver run_results más abajo para que el
    resultado sirva de algo igualmente. persist=True reutiliza el mismo
    camino que el barrido diario (persistence.persist_scrape_results) y
    dispara notificaciones/matching."""
    _check_auth(authorization)
    try:
        run_id = scheduler.launch_single_store(label, persist=persist)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"tienda desconocida: {label}")
    return {"run_id": run_id}


@app.get("/jobs/runs")
def list_runs(limit: int = 20, authorization: str | None = Header(None)) -> dict:
    """Historial de ejecuciones, más reciente primero -- alimenta la tabla de
    GET /admin/jobs en el panel."""
    _check_auth(authorization)
    conn = persistence.get_connection()
    try:
        runs = persistence.list_scrape_runs(conn, limit=limit)
    finally:
        conn.close()
    return {"runs": runs}


def _with_page_progress(conn, run: dict) -> dict:
    """Añade current_page/estimated_total_pages a la fila de un
    'single_store' en curso (docs/propuestas/propuesta-scraping-manual-panel.md
    punto 4) -- current_page viene de live_progress (StoreLogger.log() ya
    lo va registrando mientras el scrape corre); estimated_total_pages
    prefiere el total REAL si el propio mensaje de la página ya lo traía
    (PrestaShop, generic_jsonld: "página 3/12"), y si no cae al último
    recuento conocido cacheado en store.last_known_page_count. Sin efecto en
    barridos completos (esos ya tienen stores_done/stores_total, un caso
    determinado sin necesitar nada de esto) ni en runs ya terminados."""
    if run["job_type"] != "single_store" or run["status"] != "running" or not run["store_label"]:
        return run

    live = live_progress.get_current_page(run["store_label"])
    current_page, live_total = live if live is not None else (None, None)
    cached_estimate = persistence.get_store_last_known_page_count(conn, run["store_label"])

    return {
        **run,
        "current_page": current_page,
        "estimated_total_pages": live_total if live_total is not None else cached_estimate,
    }


def _with_results(run: dict) -> dict:
    """Añade results/persisted a la fila de un 'single_store' YA TERMINADO
    (docs/propuestas/propuesta-scraping-manual-panel.md, ampliación
    2026-08-29: "mostrar los resultados aunque no se persistan"). Vienen de
    run_results.py (en memoria, poblado por scheduler.launch_single_store al
    terminar) -- None si el proceso se reinició entretanto o si nunca hubo
    resultado que guardar (tienda vacía)."""
    if run["job_type"] != "single_store" or run["status"] == "running":
        return run

    result = run_results.get_results(run["id"])
    if result is None:
        return run

    return {**run, "results": result["products"], "persisted": result["persisted"]}


@app.get("/jobs/runs/{run_id}")
def get_run(run_id: int, authorization: str | None = Header(None)) -> dict:
    _check_auth(authorization)
    conn = persistence.get_connection()
    try:
        run = persistence.get_scrape_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"scrape_run {run_id} no encontrado")
        return _with_results(_with_page_progress(conn, run))
    finally:
        conn.close()


@app.get("/jobs/runs/{run_id}/log")
def get_run_log(run_id: int, tail: int = 200, authorization: str | None = Header(None)) -> dict:
    """Últimas `tail` líneas del log de esta ejecución -- no el fichero
    completo, que puede crecer bastante en un barrido diario largo."""
    _check_auth(authorization)
    run = _run_or_404(run_id)

    path = run["log_file_path"] or log_path(run_id)
    if not os.path.exists(path):
        return {"lines": []}
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    return {"lines": [line.rstrip("\n") for line in lines[-tail:]]}

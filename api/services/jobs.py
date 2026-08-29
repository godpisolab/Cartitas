"""Cliente HTTP hacia el servicio interno de jobs de `store_monitor/`
(docs/propuestas/propuesta-scraping-manual-panel.md punto 3) -- `api/` nunca
importa nada de `store_monitor/` (dependencias pesadas: `cloudscraper`,
`pybreaker`, ver docs/plan-cierre-panel-gestor.md sección "aplazado
explícitamente"), le habla solo por HTTP."""

from __future__ import annotations

import httpx

from config import JOBS_API_TOKEN, JOBS_API_URL
from errors import BadGatewayError, NotFoundError

# Las peticiones a jobs_api.py solo crean/consultan la fila scrape_run --
# nunca esperan a que el scrape en sí termine (siempre lanzado en background,
# ver scheduler.launch_*), así que un timeout corto es de sobra.
_TIMEOUT = 10.0


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {JOBS_API_TOKEN}"} if JOBS_API_TOKEN else {}


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        resp = httpx.request(method, f"{JOBS_API_URL}{path}", headers=_headers(), timeout=_TIMEOUT, **kwargs)
    except httpx.RequestError as e:
        raise BadGatewayError(f"no se pudo contactar con el servicio de jobs: {e}") from e

    if resp.status_code == 404:
        raise NotFoundError(resp.json().get("detail", "no encontrado"))
    if resp.is_error:
        raise BadGatewayError(f"el servicio de jobs devolvió {resp.status_code}: {resp.text}")
    return resp


def trigger_daily_sweep() -> int:
    return _request("POST", "/jobs/daily-sweep").json()["run_id"]


def trigger_hot_refresh() -> int:
    return _request("POST", "/jobs/hot-refresh").json()["run_id"]


def trigger_sitemap_poll() -> int:
    return _request("POST", "/jobs/sitemap-poll").json()["run_id"]


def trigger_store_scrape(label: str, *, persist: bool = False) -> int:
    """persist=False (por defecto): disparo de solo diagnóstico -- nada se
    escribe en store_product/price_history, el resultado se ve igualmente
    en GET /jobs/runs/{run_id} (campo `results`) una vez termina."""
    return _request("POST", f"/jobs/store/{label}", params={"persist": persist}).json()["run_id"]


def get_run(run_id: int) -> dict:
    return _request("GET", f"/jobs/runs/{run_id}").json()


def get_run_log(run_id: int, tail: int = 200) -> list[str]:
    return _request("GET", f"/jobs/runs/{run_id}/log", params={"tail": tail}).json()["lines"]


def list_runs(limit: int = 20) -> list[dict]:
    return _request("GET", "/jobs/runs", params={"limit": limit}).json()["runs"]

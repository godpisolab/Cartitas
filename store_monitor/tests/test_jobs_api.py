"""Tests de jobs_api.py -- el servicio HTTP interno del punto 3 (docs/
propuestas/propuesta-scraping-manual-panel.md). Todo mockeado (scheduler.launch_*,
persistence.get_scrape_run) -- no toca Postgres real ni lanza hilos de
verdad; eso ya lo cubren los tests de scheduler.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import jobs_api
import matcher
import persistence
import scheduler


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(jobs_api, "JOBS_API_TOKEN", None)
    return TestClient(jobs_api.app)


class TestTriggerEndpoints:
    @pytest.mark.parametrize("path, launch_fn", [
        ("/jobs/daily-sweep", "launch_daily_sweep"),
        ("/jobs/hot-refresh", "launch_hot_refresh"),
        ("/jobs/sitemap-poll", "launch_sitemap_poll"),
    ])
    def test_devuelve_run_id_del_launcher_correspondiente(self, client, monkeypatch, path, launch_fn):
        monkeypatch.setattr(scheduler, launch_fn, MagicMock(return_value=42))

        resp = client.post(path)

        assert resp.status_code == 200
        assert resp.json() == {"run_id": 42}

    def test_trigger_single_store_ok(self, client, monkeypatch):
        monkeypatch.setattr(scheduler, "launch_single_store", MagicMock(return_value=7))

        resp = client.post("/jobs/store/Cardzone")

        assert resp.status_code == 200
        assert resp.json() == {"run_id": 7}

    def test_trigger_single_store_persist_por_defecto_false(self, client, monkeypatch):
        mock_launch = MagicMock(return_value=7)
        monkeypatch.setattr(scheduler, "launch_single_store", mock_launch)

        client.post("/jobs/store/Cardzone")

        mock_launch.assert_called_once_with("Cardzone", persist=False)

    def test_trigger_single_store_persist_true_se_pasa_al_launcher(self, client, monkeypatch):
        mock_launch = MagicMock(return_value=7)
        monkeypatch.setattr(scheduler, "launch_single_store", mock_launch)

        client.post("/jobs/store/Cardzone", params={"persist": "true"})

        mock_launch.assert_called_once_with("Cardzone", persist=True)

    def test_trigger_single_store_desconocida_404(self, client, monkeypatch):
        monkeypatch.setattr(scheduler, "launch_single_store", MagicMock(side_effect=ValueError("nope")))

        resp = client.post("/jobs/store/NoExiste")

        assert resp.status_code == 404


class TestAuth:
    def test_sin_token_configurado_no_exige_header(self, client, monkeypatch):
        monkeypatch.setattr(scheduler, "launch_daily_sweep", MagicMock(return_value=1))
        resp = client.post("/jobs/daily-sweep")
        assert resp.status_code == 200

    def test_con_token_configurado_exige_bearer_correcto(self, monkeypatch):
        monkeypatch.setattr(jobs_api, "JOBS_API_TOKEN", "secreto")
        monkeypatch.setattr(scheduler, "launch_daily_sweep", MagicMock(return_value=1))
        client = TestClient(jobs_api.app)

        sin_auth = client.post("/jobs/daily-sweep")
        assert sin_auth.status_code == 401

        auth_mala = client.post("/jobs/daily-sweep", headers={"Authorization": "Bearer incorrecto"})
        assert auth_mala.status_code == 401

        auth_buena = client.post("/jobs/daily-sweep", headers={"Authorization": "Bearer secreto"})
        assert auth_buena.status_code == 200


class TestRunMatching:
    """POST /jobs/run-matching -- a diferencia de los jobs de scraping, corre
    síncrono (sin hilo de fondo, sin scrape_run) porque matcher.run_matching
    no hace red, solo una pasada de SQL."""

    def test_devuelve_los_contadores_de_run_matching(self, client, monkeypatch):
        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        monkeypatch.setattr(matcher, "run_matching", MagicMock(return_value={"confirmed": 3, "needs_review": 1}))

        resp = client.post("/jobs/run-matching")

        assert resp.status_code == 200
        assert resp.json() == {"counts": {"confirmed": 3, "needs_review": 1}}

    def test_cierra_la_conexion_incluso_si_run_matching_revienta(self, client, monkeypatch):
        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        monkeypatch.setattr(matcher, "run_matching", MagicMock(side_effect=RuntimeError("boom")))

        with pytest.raises(RuntimeError):
            client.post("/jobs/run-matching")

        fake_conn.close.assert_called_once()


class TestListRuns:
    def test_devuelve_las_filas_del_historial(self, client, monkeypatch):
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "list_scrape_runs", MagicMock(return_value=[
            {"id": 2, "job_type": "daily_sweep", "status": "completed"},
            {"id": 1, "job_type": "sitemap_poll", "status": "failed"},
        ]))

        resp = client.get("/jobs/runs")

        assert resp.status_code == 200
        assert [r["id"] for r in resp.json()["runs"]] == [2, 1]


class TestGetRun:
    def test_devuelve_la_fila_tal_cual(self, client, monkeypatch):
        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 5, "job_type": "daily_sweep", "status": "running",
            "stores_done": 2, "stores_total": 10,
        }))

        resp = client.get("/jobs/runs/5")

        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        assert resp.json()["stores_done"] == 2

    def test_run_inexistente_404(self, client, monkeypatch):
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value=None))

        resp = client.get("/jobs/runs/999")

        assert resp.status_code == 404


class TestGetRunPageProgress:
    """docs/propuestas/propuesta-scraping-manual-panel.md punto 4 -- un
    'single_store' en curso lleva current_page/estimated_total_pages
    añadidos a la fila de scrape_run."""

    def test_single_store_en_curso_con_progreso_en_vivo_y_total_real(self, client, monkeypatch):
        import live_progress
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 5, "job_type": "single_store", "store_label": "Cardzone", "status": "running",
            "stores_done": 0, "stores_total": None,
        }))
        monkeypatch.setattr(persistence, "get_store_last_known_page_count", MagicMock(return_value=20))
        live_progress.set_current_page("Cardzone", 3, total=12)  # prestashop: total real ya conocido
        try:
            resp = client.get("/jobs/runs/5")
        finally:
            live_progress.clear_current_page("Cardzone")

        body = resp.json()
        assert body["current_page"] == 3
        assert body["estimated_total_pages"] == 12  # el total REAL gana al estimado cacheado

    def test_single_store_en_curso_sin_total_real_usa_el_cacheado(self, client, monkeypatch):
        import live_progress
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 6, "job_type": "single_store", "store_label": "Arte9", "status": "running",
            "stores_done": 0, "stores_total": None,
        }))
        monkeypatch.setattr(persistence, "get_store_last_known_page_count", MagicMock(return_value=15))
        live_progress.set_current_page("Arte9", 4)  # sin total (woo/shopify/odoo/opencart)
        try:
            resp = client.get("/jobs/runs/6")
        finally:
            live_progress.clear_current_page("Arte9")

        body = resp.json()
        assert body["current_page"] == 4
        assert body["estimated_total_pages"] == 15

    def test_daily_sweep_en_curso_no_lleva_current_page(self, client, monkeypatch):
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 7, "job_type": "daily_sweep", "store_label": None, "status": "running",
            "stores_done": 2, "stores_total": 6,
        }))

        resp = client.get("/jobs/runs/7")

        assert "current_page" not in resp.json()

    def test_single_store_terminado_no_lleva_current_page(self, client, monkeypatch):
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 8, "job_type": "single_store", "store_label": "Cardzone", "status": "completed",
            "stores_done": 0, "stores_total": None,
        }))

        resp = client.get("/jobs/runs/8")

        assert "current_page" not in resp.json()


class TestGetRunResults:
    """docs/propuestas/propuesta-scraping-manual-panel.md, ampliación
    2026-08-29: results/persisted en la fila de un 'single_store' terminado,
    sacados de run_results.py (en memoria)."""

    def test_single_store_terminado_con_resultados_los_incluye(self, client, monkeypatch):
        import run_results
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 11, "job_type": "single_store", "store_label": "Cardzone", "status": "completed",
            "stores_done": 0, "stores_total": None,
        }))
        run_results.set_results(11, [{"name": "Booster Box"}], persisted=False)

        resp = client.get("/jobs/runs/11")

        body = resp.json()
        assert body["results"] == [{"name": "Booster Box"}]
        assert body["persisted"] is False

    def test_single_store_terminado_sin_resultados_en_memoria_no_los_incluye(self, client, monkeypatch):
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 12, "job_type": "single_store", "store_label": "Cardzone", "status": "completed",
            "stores_done": 0, "stores_total": None,
        }))

        resp = client.get("/jobs/runs/12")

        assert "results" not in resp.json()

    def test_single_store_en_curso_no_lleva_results(self, client, monkeypatch):
        import run_results
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 13, "job_type": "single_store", "store_label": "Cardzone", "status": "running",
            "stores_done": 0, "stores_total": None,
        }))
        run_results.set_results(13, [{"name": "no debería verse todavía"}], persisted=False)

        resp = client.get("/jobs/runs/13")

        assert "results" not in resp.json()

    def test_daily_sweep_terminado_no_lleva_results(self, client, monkeypatch):
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 14, "job_type": "daily_sweep", "store_label": None, "status": "completed",
            "stores_done": 6, "stores_total": 6,
        }))

        resp = client.get("/jobs/runs/14")

        assert "results" not in resp.json()


class TestGetRunLog:
    def test_fichero_inexistente_devuelve_lista_vacia(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 5, "log_file_path": str(tmp_path / "no-existe.log"),
        }))

        resp = client.get("/jobs/runs/5/log")

        assert resp.status_code == 200
        assert resp.json() == {"lines": []}

    def test_devuelve_solo_las_ultimas_tail_lineas(self, client, monkeypatch, tmp_path):
        log_file = tmp_path / "run.log"
        log_file.write_text("\n".join(f"linea {i}" for i in range(10)) + "\n", encoding="utf-8")
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value={
            "id": 5, "log_file_path": str(log_file),
        }))

        resp = client.get("/jobs/runs/5/log", params={"tail": 3})

        assert resp.json() == {"lines": ["linea 7", "linea 8", "linea 9"]}

    def test_run_inexistente_404(self, client, monkeypatch):
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(persistence, "get_scrape_run", MagicMock(return_value=None))

        resp = client.get("/jobs/runs/999/log")

        assert resp.status_code == 404

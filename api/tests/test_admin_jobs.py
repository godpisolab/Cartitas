"""Tests del panel de jobs (HTML) -- disparo manual + historial (docs/
propuestas/propuesta-scraping-manual-panel.md punto 3). services/jobs.py
(cliente HTTP hacia store_monitor/jobs_api.py) va mockeado -- lo que se
prueba aquí es el ruteo/plantillas de admin/, no la integración HTTP real
(eso ya lo cubre test_jobs_service.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import services.jobs as jobs_service
from errors import BadGatewayError


class TestAdminJobsAuth:
    def test_listado_sin_credenciales_es_401(self, client):
        assert client.get("/admin/jobs").status_code == 401

    def test_disparo_sin_credenciales_es_401(self, client):
        assert client.post("/admin/jobs/daily-sweep").status_code == 401


class TestAdminJobsList:
    def test_muestra_el_historial(self, client, admin_credentials, monkeypatch):
        monkeypatch.setattr(jobs_service, "list_runs", MagicMock(return_value=[
            {"id": 2, "job_type": "daily_sweep", "store_label": None, "status": "completed",
             "stores_done": 10, "stores_total": 10, "started_at": "2026-08-29T04:00:00", "finished_at": "..."},
            {"id": 1, "job_type": "single_store", "store_label": "Cardzone", "status": "failed",
             "stores_done": 0, "stores_total": None, "started_at": "2026-08-28T10:00:00", "finished_at": "..."},
        ]))

        resp = client.get("/admin/jobs", auth=admin_credentials)

        assert resp.status_code == 200
        assert "daily_sweep" in resp.text
        assert "Cardzone" in resp.text
        assert "10 / 10" in resp.text


class TestServicioInternoCaido:
    """Bug real reportado 2026-08-29: con jobs_api.py (store_monitor/) caído,
    el botón "Lanzar scrape ahora" no hacía nada -- BadGatewayError se
    convertía en un 502 JSON vía el handler global de ApiError, y htmx no
    hace swap de respuestas de error por defecto. La corrección: las rutas
    de admin/ capturan ApiError y devuelven 200 con un mensaje visible."""

    def test_list_jobs_muestra_banner_en_vez_de_reventar(self, client, admin_credentials, monkeypatch):
        monkeypatch.setattr(jobs_service, "list_runs", MagicMock(
            side_effect=BadGatewayError("no se pudo contactar con el servicio de jobs")
        ))

        resp = client.get("/admin/jobs", auth=admin_credentials)

        assert resp.status_code == 200
        assert "no se pudo contactar" in resp.text

    def test_trigger_devuelve_200_con_mensaje_visible_no_502_silencioso(self, client, admin_credentials,
                                                                          monkeypatch):
        monkeypatch.setattr(jobs_service, "trigger_daily_sweep", MagicMock(
            side_effect=BadGatewayError("no se pudo contactar con el servicio de jobs")
        ))

        resp = client.post("/admin/jobs/daily-sweep", auth=admin_credentials)

        # 200 a propósito -- htmx solo hace swap de respuestas 2xx por defecto,
        # así que un 502 aquí reproduciría exactamente "el botón no hace nada".
        assert resp.status_code == 200
        assert "no se pudo contactar" in resp.text


class TestAdminJobsTrigger:
    def test_daily_sweep_devuelve_el_fragmento_con_el_run(self, client, admin_credentials, monkeypatch):
        monkeypatch.setattr(jobs_service, "trigger_daily_sweep", MagicMock(return_value=42))
        monkeypatch.setattr(jobs_service, "get_run", MagicMock(return_value={
            "id": 42, "job_type": "daily_sweep", "store_label": None, "status": "running",
            "stores_done": 0, "stores_total": 6, "finished_at": None,
        }))

        resp = client.post("/admin/jobs/daily-sweep", auth=admin_credentials)

        assert resp.status_code == 200
        assert "run-42" in resp.text
        assert 'hx-get="/admin/jobs/runs/42"' in resp.text  # sigue "running" -> auto-refresco

    def test_run_terminado_no_lleva_polling(self, client, admin_credentials, monkeypatch):
        monkeypatch.setattr(jobs_service, "get_run", MagicMock(return_value={
            "id": 42, "job_type": "daily_sweep", "store_label": None, "status": "completed",
            "stores_done": 6, "stores_total": 6, "finished_at": "2026-08-29T04:10:00",
        }))

        resp = client.get("/admin/jobs/runs/42", auth=admin_credentials)

        assert resp.status_code == 200
        assert "hx-get" not in resp.text


class TestAdminJobsPageProgress:
    """docs/propuestas/propuesta-scraping-manual-panel.md punto 4: la
    plantilla del fragmento sabe pintar el progreso de página de un
    'single_store' en curso, cuando services/jobs.py lo trae."""

    def test_con_total_estimado_pinta_progress_bar(self, client, admin_credentials, monkeypatch):
        monkeypatch.setattr(jobs_service, "get_run", MagicMock(return_value={
            "id": 9, "job_type": "single_store", "store_label": "Cardzone", "status": "running",
            "stores_done": 0, "stores_total": None, "finished_at": None,
            "current_page": 4, "estimated_total_pages": 15,
        }))

        resp = client.get("/admin/jobs/runs/9", auth=admin_credentials)

        assert resp.status_code == 200
        assert "Página 4 de ~15 (estimado)" in resp.text
        assert '<progress value="4" max="15">' in resp.text

    def test_sin_estimado_todavia_muestra_solo_la_pagina_actual(self, client, admin_credentials, monkeypatch):
        monkeypatch.setattr(jobs_service, "get_run", MagicMock(return_value={
            "id": 10, "job_type": "single_store", "store_label": "TiendaNueva", "status": "running",
            "stores_done": 0, "stores_total": None, "finished_at": None,
            "current_page": 2, "estimated_total_pages": None,
        }))

        resp = client.get("/admin/jobs/runs/10", auth=admin_credentials)

        assert "Página 2" in resp.text
        assert "de ~" not in resp.text


class TestAdminJobsResults:
    """docs/propuestas/propuesta-scraping-manual-panel.md, ampliación
    2026-08-29: si no se persiste, el resultado del scrape se ve igual."""

    def test_terminado_sin_persistir_muestra_tabla_y_aviso_de_solo_vista_previa(self, client, admin_credentials,
                                                                                  monkeypatch):
        monkeypatch.setattr(jobs_service, "get_run", MagicMock(return_value={
            "id": 15, "job_type": "single_store", "store_label": "Cardzone", "status": "completed",
            "stores_done": 0, "stores_total": None, "finished_at": "2026-08-29T12:00:00",
            "results": [{"name": "Booster Box OP16", "variant": None, "price": 109.9,
                         "stock_status": "disponible", "url": "https://cardzone.example/p1"}],
            "persisted": False,
        }))

        resp = client.get("/admin/jobs/runs/15", auth=admin_credentials)

        assert resp.status_code == 200
        assert "Solo vista previa" in resp.text
        assert "Booster Box OP16" in resp.text
        assert "109.90" in resp.text
        assert "https://cardzone.example/p1" in resp.text

    def test_terminado_persistiendo_muestra_aviso_de_guardado(self, client, admin_credentials, monkeypatch):
        monkeypatch.setattr(jobs_service, "get_run", MagicMock(return_value={
            "id": 16, "job_type": "single_store", "store_label": "Cardzone", "status": "completed",
            "stores_done": 0, "stores_total": None, "finished_at": "2026-08-29T12:00:00",
            "results": [{"name": "Booster Box", "variant": None, "price": 100.0,
                         "stock_status": "disponible", "url": "https://cardzone.example/p1"}],
            "persisted": True,
        }))

        resp = client.get("/admin/jobs/runs/16", auth=admin_credentials)

        assert "Guardado" in resp.text
        assert "Solo vista previa" not in resp.text

    def test_sin_productos_encontrados(self, client, admin_credentials, monkeypatch):
        monkeypatch.setattr(jobs_service, "get_run", MagicMock(return_value={
            "id": 17, "job_type": "single_store", "store_label": "TiendaVacia", "status": "completed",
            "stores_done": 0, "stores_total": None, "finished_at": "2026-08-29T12:00:00",
            "results": [], "persisted": False,
        }))

        resp = client.get("/admin/jobs/runs/17", auth=admin_credentials)

        assert "Sin productos encontrados" in resp.text

    def test_run_sin_clave_results_no_pinta_nada_de_esto(self, client, admin_credentials, monkeypatch):
        monkeypatch.setattr(jobs_service, "get_run", MagicMock(return_value={
            "id": 18, "job_type": "daily_sweep", "store_label": None, "status": "completed",
            "stores_done": 6, "stores_total": 6, "finished_at": "2026-08-29T12:00:00",
        }))

        resp = client.get("/admin/jobs/runs/18", auth=admin_credentials)

        assert "Solo vista previa" not in resp.text
        assert "Guardado" not in resp.text
        assert "Sin productos encontrados" not in resp.text


class TestAdminStoreScrapeTrigger:
    def test_lanza_el_scrape_de_la_tienda_y_devuelve_el_fragmento(self, session, client, admin_credentials,
                                                                    monkeypatch):
        from tests.test_admin_stores import seed_store
        store_id = seed_store(session, name="Cardzone")

        mock_trigger = MagicMock(return_value=7)
        monkeypatch.setattr(jobs_service, "trigger_store_scrape", mock_trigger)
        monkeypatch.setattr(jobs_service, "get_run", MagicMock(return_value={
            "id": 7, "job_type": "single_store", "store_label": "Cardzone", "status": "running",
            "stores_done": 0, "stores_total": None, "finished_at": None,
        }))

        resp = client.post(f"/admin/stores/{store_id}/scrape", auth=admin_credentials)

        assert resp.status_code == 200
        assert "run-7" in resp.text
        mock_trigger.assert_called_once_with("Cardzone", persist=False)

    def test_checkbox_guardar_resultados_marcada_pasa_persist_true(self, session, client, admin_credentials,
                                                                      monkeypatch):
        from tests.test_admin_stores import seed_store
        store_id = seed_store(session, name="Cardzone")

        mock_trigger = MagicMock(return_value=7)
        monkeypatch.setattr(jobs_service, "trigger_store_scrape", mock_trigger)
        monkeypatch.setattr(jobs_service, "get_run", MagicMock(return_value={
            "id": 7, "job_type": "single_store", "store_label": "Cardzone", "status": "running",
            "stores_done": 0, "stores_total": None, "finished_at": None,
        }))

        client.post(f"/admin/stores/{store_id}/scrape", data={"persist": "true"}, auth=admin_credentials)

        mock_trigger.assert_called_once_with("Cardzone", persist=True)

    def test_servicio_interno_caido_muestra_mensaje_en_vez_de_no_hacer_nada(self, session, client,
                                                                              admin_credentials, monkeypatch):
        from tests.test_admin_stores import seed_store
        store_id = seed_store(session, name="Cardzone")
        monkeypatch.setattr(jobs_service, "trigger_store_scrape", MagicMock(
            side_effect=BadGatewayError("no se pudo contactar con el servicio de jobs")
        ))

        resp = client.post(f"/admin/stores/{store_id}/scrape", auth=admin_credentials)

        assert resp.status_code == 200
        assert "no se pudo contactar" in resp.text

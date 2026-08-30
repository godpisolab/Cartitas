"""Tests de services/jobs.py -- cliente HTTP hacia store_monitor/jobs_api.py
(docs/propuestas/propuesta-scraping-manual-panel.md punto 3). httpx.request
mockeado -- el riesgo aquí es el mapeo de status code -> excepción de
dominio, no la librería HTTP en sí."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

import services.jobs as jobs_service
from errors import BadGatewayError, NotFoundError


def fake_response(status_code=200, json_body=None, text_body=""):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_error = status_code >= 400
    resp.json.return_value = json_body if json_body is not None else {}
    resp.text = text_body
    return resp


class TestTriggers:
    def test_trigger_daily_sweep_devuelve_run_id(self, monkeypatch):
        monkeypatch.setattr(httpx, "request", MagicMock(return_value=fake_response(json_body={"run_id": 12})))
        assert jobs_service.trigger_daily_sweep() == 12

    def test_trigger_store_scrape_usa_la_ruta_con_el_label(self, monkeypatch):
        mock_request = MagicMock(return_value=fake_response(json_body={"run_id": 5}))
        monkeypatch.setattr(httpx, "request", mock_request)

        run_id = jobs_service.trigger_store_scrape("Cardzone")

        assert run_id == 5
        args, _kwargs = mock_request.call_args
        assert args[1].endswith("/jobs/store/Cardzone")

    def test_trigger_store_scrape_persist_por_defecto_false(self, monkeypatch):
        mock_request = MagicMock(return_value=fake_response(json_body={"run_id": 5}))
        monkeypatch.setattr(httpx, "request", mock_request)

        jobs_service.trigger_store_scrape("Cardzone")

        assert mock_request.call_args.kwargs["params"] == {"persist": False}

    def test_trigger_store_scrape_persist_true_se_pasa_como_query_param(self, monkeypatch):
        mock_request = MagicMock(return_value=fake_response(json_body={"run_id": 5}))
        monkeypatch.setattr(httpx, "request", mock_request)

        jobs_service.trigger_store_scrape("Cardzone", persist=True)

        assert mock_request.call_args.kwargs["params"] == {"persist": True}

    def test_tienda_desconocida_404_se_traduce_a_not_found_error(self, monkeypatch):
        monkeypatch.setattr(httpx, "request", MagicMock(
            return_value=fake_response(status_code=404, json_body={"detail": "tienda desconocida: X"})
        ))

        with pytest.raises(NotFoundError):
            jobs_service.trigger_store_scrape("X")

    def test_servicio_caido_se_traduce_a_bad_gateway(self, monkeypatch):
        monkeypatch.setattr(httpx, "request", MagicMock(side_effect=httpx.ConnectError("no route")))

        with pytest.raises(BadGatewayError):
            jobs_service.trigger_daily_sweep()

    def test_error_5xx_del_servicio_se_traduce_a_bad_gateway(self, monkeypatch):
        monkeypatch.setattr(httpx, "request", MagicMock(return_value=fake_response(status_code=500)))

        with pytest.raises(BadGatewayError):
            jobs_service.trigger_daily_sweep()

    def test_trigger_run_matching_devuelve_los_contadores(self, monkeypatch):
        mock_request = MagicMock(return_value=fake_response(
            json_body={"counts": {"confirmed": 3, "needs_review": 1}}
        ))
        monkeypatch.setattr(httpx, "request", mock_request)

        counts = jobs_service.trigger_run_matching()

        assert counts == {"confirmed": 3, "needs_review": 1}
        args, _kwargs = mock_request.call_args
        assert args[1].endswith("/jobs/run-matching")

    def test_trigger_run_matching_usa_un_timeout_mas_largo(self, monkeypatch):
        # No es un scrape (sin red), pero sí una pasada de SQL sobre el
        # catálogo entero -- el timeout corto de las demás llamadas
        # (crear/consultar una fila) se queda corto aquí.
        mock_request = MagicMock(return_value=fake_response(json_body={"counts": {}}))
        monkeypatch.setattr(httpx, "request", mock_request)

        jobs_service.trigger_run_matching()

        assert mock_request.call_args.kwargs["timeout"] == jobs_service._RUN_MATCHING_TIMEOUT
        assert mock_request.call_args.kwargs["timeout"] != jobs_service._TIMEOUT


class TestReads:
    def test_get_run_devuelve_el_dict_tal_cual(self, monkeypatch):
        run = {"id": 3, "status": "running", "stores_done": 2, "stores_total": 5}
        monkeypatch.setattr(httpx, "request", MagicMock(return_value=fake_response(json_body=run)))

        assert jobs_service.get_run(3) == run

    def test_get_run_inexistente_lanza_not_found(self, monkeypatch):
        monkeypatch.setattr(httpx, "request", MagicMock(
            return_value=fake_response(status_code=404, json_body={"detail": "no encontrado"})
        ))

        with pytest.raises(NotFoundError):
            jobs_service.get_run(999)

    def test_list_runs_devuelve_la_lista(self, monkeypatch):
        monkeypatch.setattr(httpx, "request", MagicMock(
            return_value=fake_response(json_body={"runs": [{"id": 2}, {"id": 1}]})
        ))

        assert jobs_service.list_runs() == [{"id": 2}, {"id": 1}]

    def test_get_run_log_devuelve_las_lineas(self, monkeypatch):
        monkeypatch.setattr(httpx, "request", MagicMock(
            return_value=fake_response(json_body={"lines": ["a", "b"]})
        ))

        assert jobs_service.get_run_log(3) == ["a", "b"]

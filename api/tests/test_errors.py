"""Un test por tipo de excepción -> código HTTP + forma del problem+json,
una única vez (ver docs/estandares-implementacion-api.md, sección 8) --
usa una app FastAPI mínima propia, no la app real, para no depender de
Postgres ni de ningún router concreto."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UnprocessableEntityError,
    install_exception_handlers,
)


@pytest.fixture
def error_app_client():
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/boom/{error_type}")
    def boom(error_type: str):
        errors_by_name = {
            "unauthorized": UnauthorizedError,
            "forbidden": ForbiddenError,
            "not_found": NotFoundError,
            "conflict": ConflictError,
            "unprocessable": UnprocessableEntityError,
        }
        raise errors_by_name[error_type]("detalle de prueba")

    @app.get("/validation-error")
    def validation_error(limit: int):
        return {"limit": limit}

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("error_type,expected_status", [
    ("unauthorized", 401),
    ("forbidden", 403),
    ("not_found", 404),
    ("conflict", 409),
    ("unprocessable", 422),
])
def test_cada_excepcion_de_dominio_mapea_al_status_correcto(error_app_client, error_type, expected_status):
    resp = error_app_client.get(f"/boom/{error_type}")

    assert resp.status_code == expected_status
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["status"] == expected_status
    assert body["detail"] == "detalle de prueba"
    assert body["type"] == "about:blank"
    assert "instance" in body


def test_error_de_validacion_de_fastapi_tambien_es_problem_json(error_app_client):
    resp = error_app_client.get("/validation-error?limit=no-es-un-numero")

    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["status"] == 422

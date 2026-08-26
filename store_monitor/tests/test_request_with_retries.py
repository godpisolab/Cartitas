"""Tests de request_with_retries() y compañía -- sección 1.4 del plan de
pruebas. HTTP mockeado con requests-mock (sin red real), time.sleep
mockeado (sin esperas reales) -- es la pieza más crítica de robustez del
scraper, y la más fácil de testear mal si no se aísla tiempo Y red a la vez.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import requests

import base_script


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Autouse en TODO este módulo: ningún test de aquí debe esperar de
    verdad. Devuelve la lista de segundos con los que se llamó a
    time.sleep, en orden, para poder verificarla."""
    calls = []
    monkeypatch.setattr(base_script.time, "sleep", lambda seconds: calls.append(seconds))
    return calls


@pytest.fixture
def session():
    return requests.Session()


class TestCaminoFeliz:
    def test_200_primer_intento_sin_reintentos(self, requests_mock, session):
        requests_mock.get("https://x.test/a", status_code=200, text="ok")
        resp = base_script.request_with_retries(session, "https://x.test/a")
        assert resp.status_code == 200
        assert requests_mock.call_count == 1

    def test_500_500_200_devuelve_el_200_final(self, requests_mock, session):
        requests_mock.get("https://x.test/a", [
            {"status_code": 500}, {"status_code": 500}, {"status_code": 200, "text": "ok"},
        ])
        resp = base_script.request_with_retries(session, "https://x.test/a", max_retries=3)
        assert resp.status_code == 200
        assert requests_mock.call_count == 3

    def test_500_en_todos_los_intentos_devuelve_la_ultima_respuesta_no_none(self, requests_mock, session):
        requests_mock.get("https://x.test/a", status_code=500)
        resp = base_script.request_with_retries(session, "https://x.test/a", max_retries=3)
        assert resp is not None
        assert resp.status_code == 500
        assert requests_mock.call_count == 3

    def test_timeout_de_red_en_todos_los_intentos_devuelve_none(self, requests_mock, session):
        requests_mock.get("https://x.test/a", exc=requests.exceptions.ConnectTimeout)
        resp = base_script.request_with_retries(session, "https://x.test/a", max_retries=3)
        assert resp is None
        assert requests_mock.call_count == 3

    def test_404_no_reintenta(self, requests_mock, session):
        requests_mock.get("https://x.test/a", status_code=404)
        resp = base_script.request_with_retries(session, "https://x.test/a", max_retries=3)
        assert resp.status_code == 404
        assert requests_mock.call_count == 1

    def test_304_no_reintenta_ni_se_trata_como_error(self, requests_mock, session):
        # A.4: un 304 de una petición condicional no es un error, es la
        # respuesta esperada cuando el recurso no cambió.
        requests_mock.get("https://x.test/a", status_code=304)
        resp = base_script.request_with_retries(session, "https://x.test/a", max_retries=3)
        assert resp.status_code == 304
        assert requests_mock.call_count == 1


class TestRetryAfter:
    def test_429_con_retry_after_en_segundos_espera_ese_tiempo_exacto(self, requests_mock, session, no_real_sleep):
        requests_mock.get("https://x.test/a", [
            {"status_code": 429, "headers": {"Retry-After": "5"}},
            {"status_code": 200, "text": "ok"},
        ])
        resp = base_script.request_with_retries(session, "https://x.test/a", max_retries=2)
        assert resp.status_code == 200
        assert no_real_sleep == [5.0]

    def test_429_con_retry_after_como_fecha_http_futura_se_parsea_a_segundos(self, requests_mock, session, no_real_sleep):
        future = datetime.now(timezone.utc) + timedelta(seconds=10)
        requests_mock.get("https://x.test/a", [
            {"status_code": 429, "headers": {"Retry-After": format_datetime(future, usegmt=True)}},
            {"status_code": 200, "text": "ok"},
        ])
        base_script.request_with_retries(session, "https://x.test/a", max_retries=2)
        assert len(no_real_sleep) == 1
        # Tolerancia: el propio test tarda una fracción de segundo en correr.
        assert 8.0 <= no_real_sleep[0] <= 10.0

    def test_429_con_retry_after_excesivo_se_corta_sin_esperar_ni_reintentar(self, requests_mock, session, no_real_sleep):
        requests_mock.get("https://x.test/a", status_code=429, headers={"Retry-After": "99999"})
        resp = base_script.request_with_retries(session, "https://x.test/a", max_retries=3)
        assert resp.status_code == 429
        assert requests_mock.call_count == 1  # se corta al primer intento
        assert no_real_sleep == []  # nunca se llama a sleep con el valor excesivo

    def test_429_sin_retry_after_usa_backoff_exponencial_normal(self, requests_mock, session, no_real_sleep):
        requests_mock.get("https://x.test/a", [
            {"status_code": 429},
            {"status_code": 200, "text": "ok"},
        ])
        base_script.request_with_retries(session, "https://x.test/a", max_retries=2)
        assert len(no_real_sleep) == 1
        assert no_real_sleep[0] > 0  # backoff exponencial + jitter, no un valor fijo


class TestParseRetryAfter:
    """_parse_retry_after() en aislado -- más rápido que pasar siempre por
    request_with_retries completo para cada variante de formato."""

    def test_segundos(self):
        assert base_script._parse_retry_after("120") == 120.0

    def test_none_si_no_hay_cabecera(self):
        assert base_script._parse_retry_after(None) is None

    def test_none_si_no_se_puede_interpretar(self):
        assert base_script._parse_retry_after("no-es-una-fecha-ni-numero") is None

    def test_fecha_pasada_devuelve_cero_no_negativo(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=30)
        result = base_script._parse_retry_after(format_datetime(past, usegmt=True))
        assert result == 0.0


class TestJsCookieChallenge:
    def test_reto_cookie_js_se_resuelve_y_reintenta_la_misma_url(self, requests_mock, session):
        challenge_html = (
            "<html><script>document.cookie='session_ok=abc123; "
            "expires=Fri, 01 Jan 2027 00:00:00 GMT; path=/; domain=.x.test';"
            "</script></html>"
        )
        requests_mock.get("https://x.test/a", [
            {"status_code": 202, "text": challenge_html},
            {"status_code": 200, "text": "ok"},
        ])
        resp = base_script.request_with_retries(session, "https://x.test/a", max_retries=2)
        assert resp.status_code == 200
        assert requests_mock.call_count == 2
        assert session.cookies.get("session_ok") == "abc123"

    def test_pagina_sin_reto_no_fija_ninguna_cookie(self):
        resp = requests.Response()
        resp._content = b"<html>pagina normal</html>"
        session = requests.Session()
        assert base_script._solve_js_cookie_challenge(session, resp) is False
        assert len(session.cookies) == 0


class TestSslFallback:
    def test_sslerror_cae_a_sesion_plana_y_reintenta(self, requests_mock, session):
        # requests-mock parchea a nivel de adapter (afecta a CUALQUIER
        # Session, incluida la "plana" que crea _get_with_ssl_fallback) --
        # primera petición falla con SSLError, la de la sesión de fallback
        # responde 200.
        requests_mock.get("https://x.test/a", [
            {"exc": requests.exceptions.SSLError},
            {"status_code": 200, "text": "ok"},
        ])
        resp = base_script.request_with_retries(session, "https://x.test/a", max_retries=2)
        assert resp.status_code == 200
        assert requests_mock.call_count == 2

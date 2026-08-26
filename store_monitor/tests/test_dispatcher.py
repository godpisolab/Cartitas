"""Tests del dispatcher -- secciones 1.5 (robots.txt/caché), 1.6
(query_store/circuit breaker) y 1.7 (run_all_stores/backoff persistido)
del plan de pruebas.

store_state.get_state/update_state se sustituyen por un dict en memoria
(fixture `fake_store_state`) en vez de ir contra Postgres real: aquí se
prueba la LÓGICA del dispatcher (cuándo cachea, cuándo reintenta, cuándo
abre el circuito), no store_state.py en sí -- eso tiene sus propios tests
de integración contra Postgres real (ver test_store_state.py)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

import dispatcher
import store_state
from domain import Platform, StoreConfig
from http_client import StoreLogger


@pytest.fixture
def fake_store_state(monkeypatch):
    data: dict[str, store_state.StoreState] = {}

    def fake_get_state(domain):
        return data.get(domain, store_state.StoreState())

    def fake_update_state(domain, **fields):
        current = data.get(domain, store_state.StoreState())
        for key, value in fields.items():
            setattr(current, key, value)
        data[domain] = current

    monkeypatch.setattr(dispatcher.store_state, "get_state", fake_get_state)
    monkeypatch.setattr(dispatcher.store_state, "update_state", fake_update_state)
    return data


@pytest.fixture(autouse=True)
def reset_breakers(monkeypatch):
    """Los circuit breakers viven en un dict global de módulo (uno por
    label) -- sin resetearlo, el estado de un test contaminaría el
    siguiente si comparten label."""
    monkeypatch.setattr(dispatcher, "_breakers", {})


def make_logger(label: str = "TiendaTest") -> StoreLogger:
    return StoreLogger(label, {})


def shopify_config(label: str = "TiendaTest") -> StoreConfig:
    return StoreConfig(label, "https://tienda-test.example", Platform.SHOPIFY,
                        shopify_collection="one-piece")


# ===========================================================================
# 1.5 -- get_robots_rules() / caché
# ===========================================================================

class TestGetRobotsRules:
    def test_primera_llamada_descarga_y_cachea(self, requests_mock, fake_store_state):
        cfg = shopify_config()
        requests_mock.get("https://tienda-test.example/robots.txt", text="User-agent: *\nDisallow:\n")
        rules = dispatcher.get_robots_rules(cfg, "https://tienda-test.example/collections/one-piece",
                                              make_logger())
        assert rules.disallowed is False
        assert requests_mock.call_count == 1
        assert fake_store_state[cfg.domain].robots_checked_at is not None

    def test_segunda_llamada_dentro_del_ttl_no_vuelve_a_pedir(self, requests_mock, fake_store_state):
        cfg = shopify_config()
        requests_mock.get("https://tienda-test.example/robots.txt", text="User-agent: *\nDisallow:\n")
        target = "https://tienda-test.example/collections/one-piece"
        dispatcher.get_robots_rules(cfg, target, make_logger())
        dispatcher.get_robots_rules(cfg, target, make_logger())
        assert requests_mock.call_count == 1

    def test_llamada_tras_expirar_el_ttl_vuelve_a_pedir(self, requests_mock, fake_store_state):
        cfg = shopify_config()
        requests_mock.get("https://tienda-test.example/robots.txt", text="User-agent: *\nDisallow:\n")
        target = "https://tienda-test.example/collections/one-piece"
        dispatcher.get_robots_rules(cfg, target, make_logger())
        # Simula que la caché es de hace más de ROBOTS_CACHE_TTL_SECONDS.
        stale = fake_store_state[cfg.domain]
        stale.robots_checked_at = time.time() - dispatcher.ROBOTS_CACHE_TTL_SECONDS - 1
        dispatcher.get_robots_rules(cfg, target, make_logger())
        assert requests_mock.call_count == 2

    def test_disallow_cubriendo_la_url_objetivo(self, requests_mock, fake_store_state):
        cfg = shopify_config()
        requests_mock.get("https://tienda-test.example/robots.txt",
                           text="User-agent: *\nDisallow: /collections/one-piece\n")
        rules = dispatcher.get_robots_rules(cfg, "https://tienda-test.example/collections/one-piece",
                                              make_logger())
        assert rules.disallowed is True

    def test_robots_txt_404_se_asume_permisivo(self, requests_mock, fake_store_state):
        cfg = shopify_config()
        requests_mock.get("https://tienda-test.example/robots.txt", status_code=404)
        rules = dispatcher.get_robots_rules(cfg, "https://tienda-test.example/collections/one-piece",
                                              make_logger())
        assert rules.disallowed is False

    def test_fallo_de_red_se_asume_permisivo_sin_abortar(self, requests_mock, fake_store_state):
        import requests
        cfg = shopify_config()
        requests_mock.get("https://tienda-test.example/robots.txt", exc=requests.exceptions.ConnectionError)
        logger = make_logger()
        rules = dispatcher.get_robots_rules(cfg, "https://tienda-test.example/collections/one-piece", logger)
        assert rules.disallowed is False
        assert "AVISO" in logger.last_error

    def test_crawl_delay_declarado_se_cachea(self, requests_mock, fake_store_state):
        cfg = shopify_config()
        requests_mock.get("https://tienda-test.example/robots.txt",
                           text="User-agent: *\nCrawl-delay: 5\n")
        rules = dispatcher.get_robots_rules(cfg, "https://tienda-test.example/collections/one-piece",
                                              make_logger())
        assert rules.crawl_delay == 5.0


# ===========================================================================
# 1.6 -- query_store() / circuit breaker
# ===========================================================================

class TestCircuitBreaker:
    def test_tres_fallos_seguidos_abre_el_circuito_sin_ejecutar_scrape_store_la_cuarta_vez(self, monkeypatch):
        cfg = shopify_config("TiendaRota")
        mock_scrape = MagicMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(dispatcher, "scrape_store", mock_scrape)

        # Las primeras BREAKER_FAIL_MAX - 1 llamadas fallan con normalidad.
        for _ in range(dispatcher.BREAKER_FAIL_MAX - 1):
            result = dispatcher.query_store(cfg, timeout=1, poll_interval=1)
            assert result.status == "error"

        # Comportamiento real de pybreaker (verificado, no asumido): la
        # llamada que ALCANZA BREAKER_FAIL_MAX ya abre el circuito en esa
        # misma invocación -- no espera a la siguiente para reportarlo.
        result = dispatcher.query_store(cfg, timeout=1, poll_interval=1)
        assert result.status == "circuit_open"
        assert mock_scrape.call_count == dispatcher.BREAKER_FAIL_MAX

        # Y la siguiente ya no ejecuta scrape_store en absoluto.
        result = dispatcher.query_store(cfg, timeout=1, poll_interval=1)
        assert result.status == "circuit_open"
        assert mock_scrape.call_count == dispatcher.BREAKER_FAIL_MAX  # sin cambios, no se llamó de nuevo

    def test_circuito_se_cierra_tras_reset_timeout(self, monkeypatch):
        monkeypatch.setattr(dispatcher, "BREAKER_RESET_TIMEOUT", 0.05)
        cfg = shopify_config("TiendaRota2")
        mock_scrape = MagicMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(dispatcher, "scrape_store", mock_scrape)

        for _ in range(dispatcher.BREAKER_FAIL_MAX):
            dispatcher.query_store(cfg, timeout=1, poll_interval=1)
        assert dispatcher.query_store(cfg, timeout=1, poll_interval=1).status == "circuit_open"

        time.sleep(0.1)  # supera el reset_timeout parcheado a 0.05s

        mock_scrape.side_effect = None
        mock_scrape.return_value = []
        result = dispatcher.query_store(cfg, timeout=1, poll_interval=1)
        assert result.status != "circuit_open"

    def test_empty_sin_motivo_no_cuenta_como_fallo_del_circuito(self, monkeypatch):
        cfg = shopify_config("TiendaVaciaLegitima")
        monkeypatch.setattr(dispatcher, "scrape_store", MagicMock(return_value=[]))

        for _ in range(dispatcher.BREAKER_FAIL_MAX + 1):
            result = dispatcher.query_store(cfg, timeout=1, poll_interval=1)
            assert result.status == "empty"
            assert result.error is None  # nunca abre el circuito

    def test_exclusion_por_robots_no_cuenta_como_fallo_del_circuito(self, monkeypatch):
        cfg = shopify_config("TiendaExcluidaPorRobots")

        def fake_scrape_store(config, logger):
            logger.log(f"{dispatcher.ROBOTS_EXCLUSION_LOG_PREFIX} (test)")
            return []

        monkeypatch.setattr(dispatcher, "scrape_store", fake_scrape_store)

        for _ in range(dispatcher.BREAKER_FAIL_MAX + 1):
            result = dispatcher.query_store(cfg, timeout=1, poll_interval=1)
            assert result.status == "empty"

        # Si contara como fallo, la última llamada habría abierto el circuito.
        assert result.status != "circuit_open"


# ===========================================================================
# 1.7 -- run_all_stores() / backoff persistido (A.3)
# ===========================================================================

class TestBackoffPersistido:
    def test_tienda_en_backoff_se_salta_sin_intentar_scraping(self, monkeypatch, fake_store_state):
        cfg = shopify_config("TiendaEnBackoff")
        fake_store_state[cfg.domain] = store_state.StoreState(backoff_until=time.time() + 900)
        mock_scrape = MagicMock(return_value=[])
        monkeypatch.setattr(dispatcher, "scrape_store", mock_scrape)

        products, failed = dispatcher.run_all_stores([cfg])

        assert products == []
        assert len(failed) == 1
        assert "backoff" in failed[0][2]
        mock_scrape.assert_not_called()

    def test_tres_fallos_en_ejecuciones_distintas_fija_backoff(self, monkeypatch, fake_store_state):
        cfg = shopify_config("TiendaQueFalla")
        monkeypatch.setattr(dispatcher, "scrape_store", MagicMock(side_effect=RuntimeError("boom")))

        for i in range(dispatcher.STORE_BACKOFF_FAILURE_THRESHOLD):
            dispatcher.run_all_stores([cfg])
            state = fake_store_state[cfg.domain]
            assert state.consecutive_failures == i + 1

        assert fake_store_state[cfg.domain].backoff_until is not None
        assert fake_store_state[cfg.domain].backoff_until > time.time()

    def test_un_exito_resetea_el_contador_de_fallos(self, monkeypatch, fake_store_state):
        cfg = shopify_config("TiendaQueSeRecupera")
        fake_store_state[cfg.domain] = store_state.StoreState(consecutive_failures=2)
        monkeypatch.setattr(dispatcher, "scrape_store", MagicMock(return_value=[
            dispatcher.Product(store=cfg.label, platform="shopify", id_product="1", name="x",
                                 variant=None, product_type="OTROS", main_set=None, set_code=None,
                                 language=None, price=1.0, stock_status="DISPONIBLE", url="https://x",
                                 sku=None, image_url=None)
        ]))

        dispatcher.run_all_stores([cfg])

        assert fake_store_state[cfg.domain].consecutive_failures == 0
        assert fake_store_state[cfg.domain].backoff_until is None

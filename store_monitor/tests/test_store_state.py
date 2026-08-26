"""Tests de store_state.py -- sección 4 del plan de pruebas, adaptada:
store_state.py se migró de un JSON local a columnas de `store` en Postgres
(2026-08-26, tras la revisión de persistencia) -- estos tests van contra
cartitas_test (ver tests/conftest.py), no contra un tmp_path."""

from __future__ import annotations

import time

import store_state
from shared.domain import Platform, StoreConfig


def make_config(domain="https://tienda.example"):
    return StoreConfig("Tienda", domain, Platform.SHOPIFY, shopify_collection="x")


class TestGetStateSinFila:
    def test_dominio_sin_fila_en_store_devuelve_valores_por_defecto(self, db_conn):
        state = store_state.get_state("https://no-existe.example")
        assert state == store_state.StoreState()

    def test_update_state_sin_fila_no_revienta(self, db_conn, capsys):
        # LIMITACIÓN documentada: sin fila de `store` todavía (tienda nueva,
        # antes del primer sync_stores), el UPDATE afecta 0 filas -- no es
        # un error, no se pierde nada porque no había nada que actualizar.
        store_state.update_state("https://no-existe.example", crawl_delay=5.0)
        # No debe lanzar excepción; no hay AVISO de fallo de conexión porque
        # la conexión SÍ funcionó, solo que no había fila que tocar.
        assert "sin conexión" not in capsys.readouterr().out


class TestGetUpdateStateRoundTrip:
    def test_update_y_get_devuelve_lo_guardado(self, db_conn):
        import persistence
        persistence.sync_stores(db_conn, [make_config()])
        db_conn.commit()

        store_state.update_state("https://tienda.example", crawl_delay=5.0)
        state = store_state.get_state("https://tienda.example")

        assert state.crawl_delay == 5.0
        assert state.disallowed is False  # resto en su valor por defecto
        assert state.consecutive_failures == 0
        assert state.active is True  # activa por defecto tras sync_stores

    def test_active_ida_y_vuelta(self, db_conn):
        import persistence
        persistence.sync_stores(db_conn, [make_config()])
        db_conn.commit()

        store_state.update_state("https://tienda.example", active=False)

        assert store_state.get_state("https://tienda.example").active is False

    def test_dos_dominios_distintos_no_se_mezclan(self, db_conn):
        import persistence
        cfg_a = make_config("https://tienda-a.example")
        cfg_b = make_config("https://tienda-b.example")
        persistence.sync_stores(db_conn, [cfg_a, cfg_b])
        db_conn.commit()

        store_state.update_state("https://tienda-a.example", consecutive_failures=3)
        store_state.update_state("https://tienda-b.example", consecutive_failures=1)

        assert store_state.get_state("https://tienda-a.example").consecutive_failures == 3
        assert store_state.get_state("https://tienda-b.example").consecutive_failures == 1

    def test_backoff_until_ida_y_vuelta_conserva_el_segundo(self, db_conn):
        import persistence
        persistence.sync_stores(db_conn, [make_config()])
        db_conn.commit()

        target = time.time() + 900
        store_state.update_state("https://tienda.example", backoff_until=target)
        state = store_state.get_state("https://tienda.example")

        assert state.backoff_until is not None
        assert abs(state.backoff_until - target) < 1.0  # tolerancia por redondeo de TIMESTAMPTZ

    def test_crawl_delay_se_redondea_a_entero_columna_integer(self, db_conn):
        import persistence
        persistence.sync_stores(db_conn, [make_config()])
        db_conn.commit()

        store_state.update_state("https://tienda.example", crawl_delay=2.6)
        state = store_state.get_state("https://tienda.example")

        assert state.crawl_delay == 3.0  # crawl_delay_seconds es INTEGER -- redondeado, no truncado


class TestDegradacionSinPostgres:
    def test_get_state_sin_postgres_devuelve_valores_por_defecto(self, monkeypatch, capsys):
        import persistence
        monkeypatch.setattr(persistence, "DATABASE_URL", "postgresql://x:x@localhost:1/nope")

        state = store_state.get_state("https://tienda.example")

        assert state == store_state.StoreState()
        assert "AVISO" in capsys.readouterr().out

    def test_update_state_sin_postgres_no_revienta(self, monkeypatch, capsys):
        import persistence
        monkeypatch.setattr(persistence, "DATABASE_URL", "postgresql://x:x@localhost:1/nope")

        store_state.update_state("https://tienda.example", crawl_delay=5.0)  # no debe lanzar

        assert "AVISO" in capsys.readouterr().out

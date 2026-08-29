"""Tests de StoreLogger (http_client.py) -- el parseo de progreso de página
en vivo (docs/propuestas/propuesta-scraping-manual-panel.md punto 4) además
del comportamiento ya existente (touch/last_error)."""

from __future__ import annotations

import pytest

import live_progress
from http_client import StoreLogger


@pytest.fixture(autouse=True)
def clear_live_progress():
    yield
    live_progress._current_page.clear()


class TestLogBasico:
    def test_touch_marca_actividad(self):
        tracker: dict[str, float] = {}
        StoreLogger("Tienda", tracker)
        assert "Tienda" in tracker

    def test_last_error_solo_con_prefijo_error_o_aviso(self):
        logger = StoreLogger("Tienda", {})
        logger.log("mensaje normal")
        assert logger.last_error is None
        logger.log("ERROR: algo falló")
        assert logger.last_error == "ERROR: algo falló"


class TestProgresoDePagina:
    """Clave por LABEL de tienda (ver live_progress.py) -- funciona igual
    con o sin run_logger asociado, no hace falta un scrape_run para que se
    registre el progreso."""

    def test_pagina_simple_sin_total(self):
        logger = StoreLogger("TiendaA", {})
        logger.log("solicitando página 4...")
        assert live_progress.get_current_page("TiendaA") == (4, None)

    def test_pagina_con_total_prestashop(self):
        logger = StoreLogger("TiendaB", {})
        logger.log("solicitando página 3/12...")
        assert live_progress.get_current_page("TiendaB") == (3, 12)

    def test_producto_n_de_m_generic_jsonld(self):
        logger = StoreLogger("TiendaC", {})
        logger.log("producto 5/23: https://tienda.example/p5")
        assert live_progress.get_current_page("TiendaC") == (5, 23)

    def test_mensaje_sin_pagina_no_actualiza_progreso_previo(self):
        logger = StoreLogger("TiendaD", {})
        logger.log("solicitando página 2...")
        logger.log("empezando (shopify)...")  # sin número de página
        assert live_progress.get_current_page("TiendaD") == (2, None)

    def test_dos_tiendas_no_se_pisan(self):
        """El motivo de indexar live_progress por label y no por run_id --
        dos StoreLogger de tiendas distintas dentro del MISMO scrape_run
        (run_all_stores, un hilo por tienda) no deben compartir entrada."""
        logger_a = StoreLogger("TiendaE", {})
        logger_b = StoreLogger("TiendaF", {})
        logger_a.log("solicitando página 7...")
        logger_b.log("solicitando página 2...")
        assert live_progress.get_current_page("TiendaE") == (7, None)
        assert live_progress.get_current_page("TiendaF") == (2, None)

"""Tests de live_progress.py -- registro en memoria del proceso del
progreso de página en vivo (docs/propuestas/propuesta-scraping-manual-panel.md
punto 4)."""

from __future__ import annotations

import live_progress


class TestLiveProgress:
    def test_set_y_get(self):
        live_progress.set_current_page("TiendaA", 4)
        assert live_progress.get_current_page("TiendaA") == (4, None)

    def test_con_total_conocido(self):
        live_progress.set_current_page("TiendaB", 3, total=12)
        assert live_progress.get_current_page("TiendaB") == (3, 12)

    def test_inexistente_devuelve_none(self):
        assert live_progress.get_current_page("NoExiste") is None

    def test_clear(self):
        live_progress.set_current_page("TiendaC", 1)
        live_progress.clear_current_page("TiendaC")
        assert live_progress.get_current_page("TiendaC") is None

    def test_clear_de_label_inexistente_no_revienta(self):
        live_progress.clear_current_page("NoExiste")  # no debe lanzar

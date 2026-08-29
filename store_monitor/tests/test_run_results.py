"""Tests de run_results.py -- productos de un disparo manual guardados en
memoria del proceso (docs/propuestas/propuesta-scraping-manual-panel.md,
ampliación 2026-08-29: ver resultados aunque no se persistan)."""

from __future__ import annotations

import run_results


class TestRunResults:
    def test_set_y_get(self):
        run_results.set_results(1, [{"name": "Booster Box"}], persisted=False)

        result = run_results.get_results(1)

        assert result == {"products": [{"name": "Booster Box"}], "persisted": False}

    def test_persisted_true(self):
        run_results.set_results(2, [{"name": "X"}], persisted=True)
        assert run_results.get_results(2)["persisted"] is True

    def test_inexistente_devuelve_none(self):
        assert run_results.get_results(999999) is None

    def test_se_puede_sobrescribir_el_mismo_run_id(self):
        run_results.set_results(3, [{"name": "primero"}], persisted=False)
        run_results.set_results(3, [{"name": "segundo"}], persisted=True)

        result = run_results.get_results(3)

        assert result["products"] == [{"name": "segundo"}]
        assert result["persisted"] is True

    def test_acotado_a_max_entries(self):
        for run_id in range(100, 130):
            run_results.set_results(run_id, [], persisted=False)

        assert len(run_results._results) <= run_results._MAX_ENTRIES
        assert run_results.get_results(129) is not None  # el más reciente sigue
        assert run_results.get_results(100) is None  # el más antiguo se descartó

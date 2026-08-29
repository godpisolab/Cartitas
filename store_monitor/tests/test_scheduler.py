"""Tests de scheduler.py -- sin cobertura hasta ahora. No se prueba
BlockingScheduler.start() (bloquea el proceso, pensado para correr para
siempre) -- se prueba que build_scheduler() conecta los 3 jobs con la
frecuencia/tipo correctos, y que cada función de job orquesta las llamadas
esperadas (con todo mockeado, sin red ni Postgres real).

Desde que job_daily_sweep/job_hot_refresh/job_sitemap_poll/job_single_store
pasan por _run_tracked() (docs/propuestas/propuesta-scraping-manual-panel.md
punto 2), cada test mockea también create_scrape_run/finish_scrape_run/
increment_scrape_run_progress y build_run_logger -- sin esto, create_scrape_run
real ejecutaría SQL contra el `fake_conn` (un MagicMock) y build_run_logger
real escribiría un fichero de log de verdad en logs/, ninguno de los dos
deseable en un test unitario."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import scheduler


@pytest.fixture
def mock_run_tracking(monkeypatch):
    """Mockea toda la maquinaria de scrape_run/logging de fichero que usa
    _run_tracked(), para que los tests de cada job puedan centrarse en el
    trabajo real que orquestan sin tocar Postgres ni el filesystem."""
    import persistence

    mocks = {
        "create_scrape_run": MagicMock(return_value=123),
        "increment_scrape_run_progress": MagicMock(),
        "finish_scrape_run": MagicMock(),
        "build_run_logger": MagicMock(return_value=MagicMock()),
        "close_run_logger": MagicMock(),
    }
    monkeypatch.setattr(persistence, "create_scrape_run", mocks["create_scrape_run"])
    monkeypatch.setattr(persistence, "increment_scrape_run_progress", mocks["increment_scrape_run_progress"])
    monkeypatch.setattr(persistence, "finish_scrape_run", mocks["finish_scrape_run"])
    monkeypatch.setattr(scheduler, "build_run_logger", mocks["build_run_logger"])
    monkeypatch.setattr(scheduler, "close_run_logger", mocks["close_run_logger"])
    return mocks


class TestBuildScheduler:
    def test_registra_los_tres_jobs_con_id_esperado(self):
        sched = scheduler.build_scheduler()
        job_ids = {job.id for job in sched.get_jobs()}
        assert job_ids == {"barrido_diario", "refresco_calientes", "polling_sitemap"}

    def test_barrido_diario_es_un_cron_no_un_intervalo(self):
        sched = scheduler.build_scheduler()
        job = sched.get_job("barrido_diario")
        assert "cron" in str(type(job.trigger)).lower()

    def test_refresco_calientes_y_sitemap_son_intervalos(self):
        sched = scheduler.build_scheduler()
        for job_id in ("refresco_calientes", "polling_sitemap"):
            job = sched.get_job(job_id)
            assert "interval" in str(type(job.trigger)).lower()

    def test_frecuencias_configurables_por_variable_de_entorno(self, monkeypatch):
        monkeypatch.setenv("HOT_REFRESH_INTERVAL_HOURS", "5")
        monkeypatch.setenv("SITEMAP_POLL_INTERVAL_HOURS", "2")
        monkeypatch.setenv("DAILY_SWEEP_HOUR", "6")
        # Los módulos leen las variables de entorno a nivel de módulo --
        # hace falta recargar para que recojan el monkeypatch de setenv.
        import importlib
        importlib.reload(scheduler)
        try:
            assert scheduler.HOT_REFRESH_INTERVAL_HOURS == 5.0
            assert scheduler.SITEMAP_POLL_INTERVAL_HOURS == 2.0
            assert scheduler.DAILY_SWEEP_HOUR == 6
        finally:
            importlib.reload(scheduler)  # restaura los valores por defecto para el resto de tests


class TestRunTracked:
    def test_crea_y_cierra_el_scrape_run_como_completed(self, mock_run_tracking):
        work = MagicMock()

        run_id = scheduler._run_tracked("daily_sweep", work, stores_total=7)

        assert run_id == 123
        mock_run_tracking["create_scrape_run"].assert_called_once_with(
            mock_run_tracking["create_scrape_run"].call_args[0][0],
            "daily_sweep", store_label=None, stores_total=7,
        )
        work.assert_called_once()
        mock_run_tracking["finish_scrape_run"].assert_called_once_with(
            mock_run_tracking["finish_scrape_run"].call_args[0][0], 123, "completed",
        )
        mock_run_tracking["close_run_logger"].assert_called_once()

    def test_excepcion_en_work_no_propaga_y_marca_failed(self, mock_run_tracking):
        work = MagicMock(side_effect=RuntimeError("boom"))

        run_id = scheduler._run_tracked("hot_refresh", work)  # no debe lanzar

        assert run_id == 123
        mock_run_tracking["finish_scrape_run"].assert_called_once_with(
            mock_run_tracking["finish_scrape_run"].call_args[0][0], 123, "failed",
        )
        mock_run_tracking["close_run_logger"].assert_called_once()


class _SyncThread:
    """Sustituye threading.Thread en los tests de launch_tracked_run: corre
    el target al momento en vez de en un hilo real, para que las
    aserciones de después de start() no compitan con el hilo de fondo."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self) -> None:
        self._target()


class TestLaunchTrackedRun:
    def test_devuelve_run_id_al_momento_y_ejecuta_work_en_background(self, monkeypatch, mock_run_tracking):
        monkeypatch.setattr(scheduler.threading, "Thread", _SyncThread)
        work = MagicMock()

        run_id = scheduler.launch_tracked_run("daily_sweep", work, stores_total=4)

        assert run_id == 123
        work.assert_called_once()
        mock_run_tracking["finish_scrape_run"].assert_called_once_with(
            mock_run_tracking["finish_scrape_run"].call_args[0][0], 123, "completed",
        )

    def test_launch_single_store_tienda_desconocida_lanza_value_error(self, monkeypatch, mock_run_tracking):
        import dispatcher
        monkeypatch.setattr(dispatcher, "find_store", MagicMock(return_value=None))

        with pytest.raises(ValueError):
            scheduler.launch_single_store("no-existe")

        mock_run_tracking["create_scrape_run"].assert_not_called()

    def test_launch_single_store_lanza_query_store_en_background(self, monkeypatch, mock_run_tracking):
        import dispatcher
        monkeypatch.setattr(scheduler.threading, "Thread", _SyncThread)
        fake_config = MagicMock(label="Cardzone")
        fake_result = MagicMock(status="ok")
        monkeypatch.setattr(dispatcher, "find_store", MagicMock(return_value=fake_config))
        mock_query = MagicMock(return_value=fake_result)
        monkeypatch.setattr(dispatcher, "query_store", mock_query)

        run_id = scheduler.launch_single_store("Cardzone")

        assert run_id == 123
        mock_query.assert_called_once()
        mock_run_tracking["create_scrape_run"].assert_called_once_with(
            mock_run_tracking["create_scrape_run"].call_args[0][0],
            "single_store", store_label="Cardzone", stores_total=None,
        )


class TestLaunchSingleStorePersistAndResults:
    """docs/propuestas/propuesta-scraping-manual-panel.md, ampliación
    2026-08-29: el botón "Lanzar scrape ahora" deja elegir si persistir, y
    el resultado (encontrado o no) queda visible en run_results.py."""

    def test_sin_persistir_guarda_resultados_pero_no_llama_a_persistencia(self, monkeypatch, mock_run_tracking):
        import dispatcher
        import persistence
        import run_results

        monkeypatch.setattr(scheduler.threading, "Thread", _SyncThread)
        mock_run_tracking["build_run_logger"].return_value.name = "scrape_run.123"
        fake_config = MagicMock(label="Cardzone")
        product = MagicMock()
        product.to_dict.return_value = {"name": "Booster Box", "price": 10.0}
        fake_result = MagicMock(status="ok", products=[product])
        monkeypatch.setattr(dispatcher, "find_store", MagicMock(return_value=fake_config))
        monkeypatch.setattr(dispatcher, "query_store", MagicMock(return_value=fake_result))
        mock_persist = MagicMock()
        monkeypatch.setattr(persistence, "persist_scrape_results", mock_persist)

        run_id = scheduler.launch_single_store("Cardzone", persist=False)

        assert run_id == 123
        mock_persist.assert_not_called()
        assert run_results.get_results(123) == {
            "products": [{"name": "Booster Box", "price": 10.0}], "persisted": False,
        }

    def test_con_persistir_llama_a_persist_scrape_results_y_notifica_y_matchea(self, monkeypatch, mock_run_tracking):
        import dispatcher
        import matcher
        import persistence
        import restock_notifier
        import run_results

        monkeypatch.setattr(scheduler.threading, "Thread", _SyncThread)
        mock_run_tracking["build_run_logger"].return_value.name = "scrape_run.123"
        fake_config = MagicMock(label="Cardzone")
        product = MagicMock()
        product.to_dict.return_value = {"name": "Booster Box"}
        fake_result = MagicMock(status="ok", products=[product])
        monkeypatch.setattr(dispatcher, "find_store", MagicMock(return_value=fake_config))
        monkeypatch.setattr(dispatcher, "query_store", MagicMock(return_value=fake_result))
        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        mock_persist = MagicMock(return_value=[99])
        monkeypatch.setattr(persistence, "persist_scrape_results", mock_persist)
        mock_notify = MagicMock()
        monkeypatch.setattr(restock_notifier, "notify_for_restock_events", mock_notify)
        mock_match = MagicMock()
        monkeypatch.setattr(matcher, "run_matching", mock_match)

        scheduler.launch_single_store("Cardzone", persist=True)

        mock_persist.assert_called_once_with([product], [fake_config])
        mock_notify.assert_called_once_with(fake_conn, [99])
        mock_match.assert_called_once_with(fake_conn)
        # fake_conn también sale de persistence.get_connection() para el resto
        # de _make_tracked_runner (progress_conn/finish_conn) -- no "una vez".
        fake_conn.close.assert_called()
        assert run_results.get_results(123)["persisted"] is True

    def test_con_persistir_pero_sin_productos_no_llama_a_persistencia(self, monkeypatch, mock_run_tracking):
        import dispatcher
        import persistence

        monkeypatch.setattr(scheduler.threading, "Thread", _SyncThread)
        fake_config = MagicMock(label="TiendaVacia")
        fake_result = MagicMock(status="empty", products=[])
        monkeypatch.setattr(dispatcher, "find_store", MagicMock(return_value=fake_config))
        monkeypatch.setattr(dispatcher, "query_store", MagicMock(return_value=fake_result))
        mock_persist = MagicMock()
        monkeypatch.setattr(persistence, "persist_scrape_results", mock_persist)

        scheduler.launch_single_store("TiendaVacia", persist=True)

        mock_persist.assert_not_called()

    def test_persist_por_defecto_es_false(self, monkeypatch, mock_run_tracking):
        import dispatcher
        import persistence

        monkeypatch.setattr(scheduler.threading, "Thread", _SyncThread)
        fake_config = MagicMock(label="Cardzone")
        product = MagicMock()
        product.to_dict.return_value = {}
        fake_result = MagicMock(status="ok", products=[product])
        monkeypatch.setattr(dispatcher, "find_store", MagicMock(return_value=fake_config))
        monkeypatch.setattr(dispatcher, "query_store", MagicMock(return_value=fake_result))
        mock_persist = MagicMock()
        monkeypatch.setattr(persistence, "persist_scrape_results", mock_persist)

        scheduler.launch_single_store("Cardzone")  # sin pasar persist

        mock_persist.assert_not_called()


class TestOnStoreDonePersistsPageCount:
    """docs/propuestas/propuesta-scraping-manual-panel.md punto 4: el
    callback on_store_done que ya usa el punto 2 para stores_done también
    refresca store.last_known_page_count con lo último que vio StoreLogger
    para esa tienda (live_progress, indexado por label)."""

    def test_con_progreso_conocido_persiste_y_limpia(self, monkeypatch, mock_run_tracking):
        import live_progress
        import persistence

        mock_update = MagicMock()
        monkeypatch.setattr(persistence, "update_store_last_known_page_count", mock_update)
        live_progress.set_current_page("TiendaX", 4, total=15)

        def work(_run_logger, on_store_done):
            on_store_done("TiendaX")

        scheduler._run_tracked("single_store", work, store_label="TiendaX")

        mock_update.assert_called_once_with(mock_update.call_args[0][0], "TiendaX", 15)
        assert live_progress.get_current_page("TiendaX") is None  # se limpia tras persistir

    def test_sin_total_conocido_usa_la_ultima_pagina_vista(self, monkeypatch, mock_run_tracking):
        import live_progress
        import persistence

        mock_update = MagicMock()
        monkeypatch.setattr(persistence, "update_store_last_known_page_count", mock_update)
        live_progress.set_current_page("TiendaY", 7)  # sin total (Woo/Shopify/Odoo/OpenCart)

        def work(_run_logger, on_store_done):
            on_store_done("TiendaY")

        scheduler._run_tracked("single_store", work, store_label="TiendaY")

        mock_update.assert_called_once_with(mock_update.call_args[0][0], "TiendaY", 7)

    def test_sin_progreso_conocido_no_llama_a_persistencia(self, monkeypatch, mock_run_tracking):
        import persistence

        mock_update = MagicMock()
        monkeypatch.setattr(persistence, "update_store_last_known_page_count", mock_update)

        def work(_run_logger, on_store_done):
            on_store_done("TiendaSinProgreso")

        scheduler._run_tracked("single_store", work, store_label="TiendaSinProgreso")

        mock_update.assert_not_called()


class TestJobHotRefresh:
    def test_llama_a_refresh_hot_products_y_notifica_y_cierra_la_conexion(self, monkeypatch, mock_run_tracking):
        import config
        import persistence
        import restock_notifier

        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        monkeypatch.setattr(persistence, "refresh_hot_products", MagicMock(return_value=({}, [42])))
        mock_notify = MagicMock()
        monkeypatch.setattr(restock_notifier, "notify_for_restock_events", mock_notify)
        monkeypatch.setattr(config, "STORES", [])

        scheduler.job_hot_refresh()

        mock_notify.assert_called_once_with(fake_conn, [42])
        fake_conn.close.assert_called()

    def test_excepcion_no_propaga_y_marca_scrape_run_como_failed(self, monkeypatch, mock_run_tracking):
        import config
        import persistence

        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        monkeypatch.setattr(persistence, "refresh_hot_products", MagicMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(config, "STORES", [])

        scheduler.job_hot_refresh()  # no debe lanzar

        fake_conn.close.assert_called()
        mock_run_tracking["finish_scrape_run"].assert_called_once_with(
            mock_run_tracking["finish_scrape_run"].call_args[0][0], 123, "failed",
        )


class TestJobSitemapPoll:
    def test_llama_a_poll_sitemaps_y_cierra_la_conexion(self, monkeypatch, mock_run_tracking):
        import config
        import persistence
        import sitemap_poller

        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        mock_poll = MagicMock()
        monkeypatch.setattr(sitemap_poller, "poll_sitemaps", mock_poll)
        monkeypatch.setattr(config, "STORES", [])

        scheduler.job_sitemap_poll()

        mock_poll.assert_called_once_with(fake_conn, [])
        fake_conn.close.assert_called()

    def test_excepcion_no_propaga(self, monkeypatch, mock_run_tracking):
        import config
        import persistence
        import sitemap_poller

        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        monkeypatch.setattr(sitemap_poller, "poll_sitemaps", MagicMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(config, "STORES", [])

        scheduler.job_sitemap_poll()  # no debe lanzar

        fake_conn.close.assert_called()


class TestJobDailySweep:
    def test_reutiliza_main_tal_cual(self, monkeypatch, mock_run_tracking):
        import base_script
        import config

        mock_main = MagicMock()
        monkeypatch.setattr(base_script, "main", mock_main)
        monkeypatch.setattr(config, "STORES", [1, 2, 3])

        scheduler.job_daily_sweep()

        mock_main.assert_called_once()
        _args, kwargs = mock_main.call_args
        assert "run_logger" in kwargs and "on_store_done" in kwargs
        mock_run_tracking["create_scrape_run"].assert_called_once_with(
            mock_run_tracking["create_scrape_run"].call_args[0][0],
            "daily_sweep", store_label=None, stores_total=3,
        )

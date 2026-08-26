"""Tests de scheduler.py -- sin cobertura hasta ahora. No se prueba
BlockingScheduler.start() (bloquea el proceso, pensado para correr para
siempre) -- se prueba que build_scheduler() conecta los 3 jobs con la
frecuencia/tipo correctos, y que cada función de job orquesta las llamadas
esperadas (con todo mockeado, sin red ni Postgres real)."""

from __future__ import annotations

from unittest.mock import MagicMock

import scheduler


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


class TestJobHotRefresh:
    def test_llama_a_refresh_hot_products_y_notifica_y_cierra_la_conexion(self, monkeypatch):
        import base_script
        import persistence
        import restock_notifier

        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        monkeypatch.setattr(persistence, "refresh_hot_products", MagicMock(return_value=({}, [42])))
        mock_notify = MagicMock()
        monkeypatch.setattr(restock_notifier, "notify_for_restock_events", mock_notify)
        monkeypatch.setattr(base_script, "STORES", [])

        scheduler.job_hot_refresh()

        mock_notify.assert_called_once_with(fake_conn, [42])
        fake_conn.close.assert_called_once()

    def test_excepcion_no_propaga_y_cierra_la_conexion_igual(self, monkeypatch, capsys):
        import base_script
        import persistence

        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        monkeypatch.setattr(persistence, "refresh_hot_products", MagicMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(base_script, "STORES", [])

        scheduler.job_hot_refresh()  # no debe lanzar

        fake_conn.close.assert_called_once()
        assert "ERROR" in capsys.readouterr().out


class TestJobSitemapPoll:
    def test_llama_a_poll_sitemaps_y_cierra_la_conexion(self, monkeypatch):
        import base_script
        import persistence
        import sitemap_poller

        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        mock_poll = MagicMock()
        monkeypatch.setattr(sitemap_poller, "poll_sitemaps", mock_poll)
        monkeypatch.setattr(base_script, "STORES", [])

        scheduler.job_sitemap_poll()

        mock_poll.assert_called_once_with(fake_conn, [])
        fake_conn.close.assert_called_once()

    def test_excepcion_no_propaga(self, monkeypatch, capsys):
        import base_script
        import persistence
        import sitemap_poller

        fake_conn = MagicMock()
        monkeypatch.setattr(persistence, "get_connection", MagicMock(return_value=fake_conn))
        monkeypatch.setattr(sitemap_poller, "poll_sitemaps", MagicMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(base_script, "STORES", [])

        scheduler.job_sitemap_poll()  # no debe lanzar

        fake_conn.close.assert_called_once()
        assert "ERROR" in capsys.readouterr().out


class TestJobDailySweep:
    def test_reutiliza_main_tal_cual(self, monkeypatch):
        import base_script
        mock_main = MagicMock()
        monkeypatch.setattr(base_script, "main", mock_main)

        scheduler.job_daily_sweep()

        mock_main.assert_called_once()

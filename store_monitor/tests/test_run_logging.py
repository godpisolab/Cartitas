"""Tests de run_logging.py -- solo run_id_from_logger() (lo demás ya se
ejercita indirectamente a través de scheduler.py y http_client.py)."""

from __future__ import annotations

import logging

from run_logging import build_run_logger, close_run_logger, console_logger, run_id_from_logger


class TestRunIdFromLogger:
    def test_recupera_el_run_id_de_un_run_logger_real(self, tmp_path, monkeypatch):
        monkeypatch.setattr("run_logging.LOG_DIR", str(tmp_path))
        run_logger = build_run_logger(456)
        try:
            assert run_id_from_logger(run_logger) == 456
        finally:
            close_run_logger(run_logger)

    def test_console_logger_devuelve_none(self):
        assert run_id_from_logger(console_logger) is None

    def test_logger_sin_prefijo_scrape_run_devuelve_none(self):
        assert run_id_from_logger(logging.getLogger("otro.logger")) is None

    def test_sufijo_no_numerico_devuelve_none(self):
        assert run_id_from_logger(logging.getLogger("scrape_run.no-es-un-numero")) is None

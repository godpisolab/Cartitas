"""Logging real a fichero, un `FileHandler` por ejecución -- reemplaza los
`print()` sueltos de dispatcher.py/StoreLogger (docs/propuestas/
propuesta-scraping-manual-panel.md punto 2). Un fichero por `run_id`
(`logs/{run_id}.log`), no uno por tienda ni uno global que crece para
siempre.

`run_id` es el id de la fila `scrape_run` correspondiente (ver
persistence.create_scrape_run) -- se conoce solo DESPUÉS del INSERT, así que
el logger se construye después de crear la fila, nunca antes.
"""

from __future__ import annotations

import logging
import os

LOG_DIR = os.environ.get("SCRAPE_LOG_DIR", "logs")
LOG_FORMAT = "%(asctime)s [%(store)s] %(message)s"


class _DefaultStoreFilter(logging.Filter):
    """Red de seguridad: el formatter exige `%(store)s` en cada registro --
    si algún día se añade una llamada `.info(...)` a este logger sin pasar
    `extra={"store": ...}`, esto evita un KeyError en vez de tirar abajo la
    ejecución completa por un fallo de logging."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "store"):
            record.store = "system"
        return True


# Fallback cuando no hay run_logger asociado (tests, un query_store() suelto
# sin ejecución de por medio) -- consola en vez de fichero, mismo formato.
console_logger = logging.getLogger("store_monitor.console")
if not console_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(store)s] %(message)s"))
    console_logger.addHandler(_handler)
    console_logger.setLevel(logging.INFO)
    console_logger.propagate = False


def log_path(run_id: int | str) -> str:
    return os.path.join(LOG_DIR, f"{run_id}.log")


def build_run_logger(run_id: int | str) -> logging.Logger:
    """Logger dedicado a esta ejecución, con su propio FileHandler. Nombrado
    por run_id para que dos ejecuciones concurrentes (ej. un barrido diario
    en curso y un disparo manual de una tienda) no compartan handlers."""
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger(f"scrape_run.{run_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.FileHandler(log_path(run_id), encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.addFilter(_DefaultStoreFilter())
        logger.addHandler(handler)
    return logger


def close_run_logger(logger: logging.Logger) -> None:
    """Cierra y desengancha los handlers de fichero -- llamar al terminar la
    ejecución para no dejar el descriptor de fichero abierto indefinidamente
    en un proceso de larga duración (scheduler.py)."""
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def run_id_from_logger(logger: logging.Logger) -> int | None:
    """Recupera el run_id codificado en logger.name (build_run_logger lo
    nombra 'scrape_run.{run_id}') -- para código que solo recibe el
    run_logger (ver scheduler.launch_single_store) y necesita asociar algo
    al run_id sin que el llamador tenga que pasarlo aparte. None si el
    logger no es uno de los nuestros (ej. console_logger) o el nombre no
    termina en un entero."""
    if not logger.name.startswith("scrape_run."):
        return None
    try:
        return int(logger.name.removeprefix("scrape_run."))
    except ValueError:
        return None

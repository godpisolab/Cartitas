"""Persistencia local (JSON) del estado dinámico por tienda entre ejecuciones.

Hoy `STORES` (base_script.py) vive solo en memoria: cada `python
base_script.py` arranca de cero, sin recordar nada del ciclo anterior. Esto
es un problema concreto para dos estándares de scraping respetuoso (ver
cambios-necesarios-scraper.md, bloque A):

- robots.txt/Crawl-delay (A.2): no tiene sentido volver a pedir robots.txt
  en cada ejecución si se comprobó hace una hora -- se cachea por tienda.
- backoff tras 429 (A.3): si una tienda nos pidió parar, el SIGUIENTE ciclo
  de scraping (no solo los reintentos de la ejecución actual) debe
  respetarlo -- para eso el estado tiene que sobrevivir al proceso.

Interfaz deliberadamente pequeña (get_state/update_state por dominio) para
que, cuando llegue la persistencia en Postgres (bloque B), sustituir este
módulo por lecturas/escrituras a la tabla `store` (que ya tiene
crawl_delay_seconds, backoff_until y robots_checked_at) no requiera tocar
ningún punto de base_script.py que ya use esta interfaz.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from typing import Optional

STATE_PATH = os.path.join(os.path.dirname(__file__), "store_state.json")

# Un único lock de proceso: run_all_stores lanza un hilo por tienda y cada
# uno puede llamar a update_state() casi a la vez. Cada hilo solo toca su
# propia clave (el dominio), pero la escritura es del fichero ENTERO, así
# que sin lock un hilo podría pisar la actualización de otro (leer-modificar-
# escribir no es atómico entre hilos sin esto).
_LOCK = threading.Lock()


@dataclass
class StoreState:
    robots_checked_at: Optional[float] = None   # unix timestamp de la última comprobación de robots.txt
    crawl_delay: Optional[float] = None          # Crawl-delay declarado por robots.txt, en segundos
    disallowed: bool = False                     # True si robots.txt prohíbe la URL que scrapeamos
    consecutive_failures: int = 0                # fallos seguidos entre ejecuciones (ver A.3)
    backoff_until: Optional[float] = None        # unix timestamp: no reintentar esta tienda antes de esto


def _read_all() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_all(data: dict) -> None:
    """Escritura atómica (fichero temporal + os.replace) -- evita dejar
    store_state.json a medio escribir si el proceso se corta a mitad de un
    run_all_stores."""
    directory = os.path.dirname(STATE_PATH)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".store_state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_path, STATE_PATH)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def get_state(domain: str) -> StoreState:
    """Devuelve el estado guardado de `domain` (la StoreConfig.domain, ya
    estable y normalizada -- ver B.1 de cambios-necesarios-scraper.md), o
    los valores por defecto si nunca se guardó nada de esta tienda."""
    with _LOCK:
        raw = _read_all().get(domain, {})
    defaults = asdict(StoreState())
    return StoreState(**{**defaults, **raw})


def update_state(domain: str, **fields) -> None:
    """Lee-modifica-escribe bajo lock: solo actualiza los campos pasados,
    conserva el resto tal cual estaban."""
    with _LOCK:
        data = _read_all()
        current = {**asdict(StoreState()), **data.get(domain, {})}
        current.update(fields)
        data[domain] = current
        _write_all(data)

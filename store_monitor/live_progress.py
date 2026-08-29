"""Progreso de página EN VIVO de una tienda que se está scrapeando ahora
mismo (docs/propuestas/propuesta-scraping-manual-panel.md punto 4, caso
"una sola tienda"). Vive en memoria del proceso -- jobs_api.py y
scheduler.py corren en el MISMO proceso (ver jobs_api.py), así que un dict
compartido basta; no hace falta persistirlo, se pierde con la ejecución en
curso si el proceso muere, que es exactamente lo que se quiere.

Poblado por StoreLogger.log() (http_client.py) parseando el número de
página que YA registra cada scraper en su mensaje normal ("...página
4...", "...página 3/12...", "...producto 5/23...") -- ningún scraper
necesita tocarse para esto, es el mismo mensaje que ya imprimían.

Clave por LABEL de tienda, no por run_id: run_all_stores() comparte un
único run_id entre todas las tiendas del barrido, scrapeadas en paralelo --
si la clave fuera run_id, sus hilos se pisarían entre sí. Por label cada
tienda tiene su propia entrada, tanto en un disparo manual (un solo label)
como dentro de un barrido completo (uno por hilo) -- y así el mismo
mecanismo sirve para refrescar store.last_known_page_count al final de
CUALQUIER scrape real, no solo el manual (ver scheduler._make_tracked_runner)."""

from __future__ import annotations

_current_page: dict[str, tuple[int, int | None]] = {}  # label -> (página actual, total real si se conoce)


def set_current_page(label: str, page: int, total: int | None = None) -> None:
    _current_page[label] = (page, total)


def get_current_page(label: str) -> tuple[int, int | None] | None:
    return _current_page.get(label)


def clear_current_page(label: str) -> None:
    _current_page.pop(label, None)

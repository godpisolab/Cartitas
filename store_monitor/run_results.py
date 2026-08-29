"""Productos encontrados por un disparo manual de UNA tienda, EN MEMORIA
(docs/propuestas/propuesta-scraping-manual-panel.md, ampliación 2026-08-29:
el botón "Lanzar scrape ahora" deja elegir si persistir o no -- si no se
persiste, el resultado tiene que verse en algún sitio para que sirva de
algo). Solo tiene sentido para 'single_store': un barrido completo siempre
persiste y ya se ve reflejado en el catálogo, no necesita este mecanismo.

Vive en memoria del proceso, igual que live_progress.py -- se pierde si el
proceso reinicia, aceptable para algo pensado como "mira lo que se acaba de
scrapear", no como registro duradero (para eso está la opción persist=True,
que sí escribe en store_product/price_history). Acotado a los últimos
_MAX_ENTRIES para no crecer sin límite en un proceso de larga duración con
muchos disparos manuales."""

from __future__ import annotations

from collections import OrderedDict
from typing import TypedDict

_MAX_ENTRIES = 20


class RunResult(TypedDict):
    products: list[dict]
    persisted: bool


_results: "OrderedDict[int, RunResult]" = OrderedDict()


def set_results(run_id: int, products: list[dict], *, persisted: bool) -> None:
    _results[run_id] = {"products": products, "persisted": persisted}
    _results.move_to_end(run_id)
    while len(_results) > _MAX_ENTRIES:
        _results.popitem(last=False)


def get_results(run_id: int) -> RunResult | None:
    return _results.get(run_id)

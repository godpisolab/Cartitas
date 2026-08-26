"""Puente deliberado y ESTRECHO hacia store_monitor/ -- reutiliza la ÚNICA
fuente de verdad de las reglas de clasificación (`classify_product`) y el
mapeo tipo de producto -> categoría (`matcher.py`) para que
`GET /matches`/`GET /matches/missing-candidates` deriven la categoría de un
`raw_name` exactamente igual que ya lo hace `matcher.run_matching()` --
reimplementar estas reglas por separado en la API arriesgaría que las dos
copias diverjan silenciosamente con el tiempo, que es peor que este
acoplamiento explícito.

NUNCA importar desde aquí nada de `dispatcher.py`/`http_client.py`/
`scrapers/` -- ese es justo el código con dependencias pesadas
(`cloudscraper`, `pybreaker`, reintentos HTTP) que `api/` evita a propósito
manteniendo un `requirements.txt` propio (ver
docs/estandares-implementacion-api.md, sección 1). `classify.py` y las
constantes de `matcher.py` usadas aquí son Python puro (sin dependencias de
terceros), así que importarlas no arrastra nada de eso.
"""

from __future__ import annotations

import sys
from pathlib import Path

_STORE_MONITOR_DIR = Path(__file__).resolve().parent.parent / "store_monitor"
# append(), NUNCA insert(0, ...): store_monitor/ tiene su PROPIO config.py
# (nombre distinto, mismo nombre de módulo) -- si se antepusiera, un
# "import config" posterior en cualquier parte de api/ podría resolver al
# config.py equivocado según el orden de ejecución. Al añadirlo al final,
# el directorio de api/ (ya en sys.path por pytest/uvicorn al arrancar
# desde dentro de api/) sigue ganando siempre esa resolución.
if str(_STORE_MONITOR_DIR) not in sys.path:
    sys.path.append(str(_STORE_MONITOR_DIR))

from classify import classify_product  # noqa: E402
from matcher import NOT_APPLICABLE_PRODUCT_TYPES, PRODUCT_TYPE_TO_CATEGORY_SLUG  # noqa: E402

__all__ = ["classify_product", "NOT_APPLICABLE_PRODUCT_TYPES", "PRODUCT_TYPE_TO_CATEGORY_SLUG"]

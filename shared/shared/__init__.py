"""Shared Kernel (DDD) entre api/ y store_monitor/: dominio y reglas de
clasificación puras -- ver domain.py y classify.py. Deliberadamente vacío
más allá de esto: si algún día hace falta importar algo pesado aquí, esa
es la señal de que ha dejado de ser un shared kernel sano (ver decisión de
arquitectura sobre el acoplamiento api/store_monitor)."""

from __future__ import annotations

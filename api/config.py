"""Configuración del servicio: conexión a Postgres y API keys estáticas por
cliente (ver docs/api-endpoints-v1.md, sección 0 -- "API key estática por
cliente, sin expiración ni flujo de login").

API_KEYS se calcula al importar el módulo a partir de la variable de
entorno API_KEYS_JSON -- los tests la sobrescriben monkeypateando
`config.API_KEYS` directamente (mismo patrón que `persistence.DATABASE_URL`
en store_monitor/), no releyendo variables de entorno en caliente.
"""

from __future__ import annotations

import json
import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    # Mismo Postgres/puerto que store_monitor/persistence.py -- un único
    # servidor Postgres para todo el proyecto, tablas compartidas.
    "postgresql://cartitas:cartitas@localhost:5433/cartitas",
)


def _load_api_keys() -> dict[str, frozenset[str]]:
    """API_KEYS_JSON: '{"clave-frontend": ["read", "write:subscriptions"], ...}'.
    Sin la variable definida, ninguna key es válida -- falla cerrado, no abierto."""
    raw = os.environ.get("API_KEYS_JSON", "{}")
    parsed = json.loads(raw)
    return {key: frozenset(scopes) for key, scopes in parsed.items()}


API_KEYS: dict[str, frozenset[str]] = _load_api_keys()

"""Auth por API key + scope (docs/api-endpoints-v1.md, sección 0): toda
petición requiere `Authorization: Bearer <apiKey>`. No identifica a una
persona (no hay login) -- identifica QUÉ APLICACIÓN CLIENTE llama, para
decidir qué acciones tiene permitidas.

`require_scope(scope)` se engancha declarativamente en cada router
(`Depends(require_scope("read"))`) -- el scope requerido queda documentado
en la propia firma del endpoint, nunca comprobado a mano dentro de él."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Header

import config
from errors import ForbiddenError, UnauthorizedError


def _extract_api_key(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Falta la cabecera 'Authorization: Bearer <apiKey>'")
    api_key = authorization.removeprefix("Bearer ").strip()
    if not api_key:
        raise UnauthorizedError("Falta la cabecera 'Authorization: Bearer <apiKey>'")
    return api_key


def require_scope(scope: str) -> Callable[..., None]:
    def _check(authorization: str | None = Header(default=None)) -> None:
        api_key = _extract_api_key(authorization)
        scopes = config.API_KEYS.get(api_key)
        if scopes is None:
            raise UnauthorizedError("API key no reconocida")
        if scope not in scopes:
            raise ForbiddenError(f"La API key no tiene el scope '{scope}'")

    return _check

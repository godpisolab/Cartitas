"""HTTP Basic para el panel de gestor -- mecanismo DISTINTO del Bearer +
scopes de `auth.py` (docs/estandares-implementacion-frontend.md sección
2.2): credencial de PERSONA, no de aplicación cliente. No reutilizar
`require_scope()` aquí a propósito."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import config

security = HTTPBasic()


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    # secrets.compare_digest en vez de `==` -- comparación en tiempo
    # constante, evita timing attacks triviales sobre la contraseña.
    correct_user = secrets.compare_digest(credentials.username, config.ADMIN_USERNAME)
    correct_pass = secrets.compare_digest(credentials.password, config.ADMIN_PASSWORD)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Credenciales inválidas", headers={"WWW-Authenticate": "Basic"})

"""Tests de auth.py -- unitario puro, sin BBDD (ver
docs/estandares-implementacion-api.md, sección 8)."""

from __future__ import annotations

import pytest

import config
from auth import require_scope
from errors import ForbiddenError, UnauthorizedError


@pytest.fixture(autouse=True)
def known_keys(monkeypatch):
    monkeypatch.setattr(config, "API_KEYS", {
        "frontend-key": frozenset({"read", "write:subscriptions"}),
        "panel-key": frozenset({"read", "write:matches", "write:products"}),
    })


class TestRequireScope:
    def test_sin_cabecera_authorization_lanza_401(self):
        check = require_scope("read")
        with pytest.raises(UnauthorizedError):
            check(authorization=None)

    def test_cabecera_sin_prefijo_bearer_lanza_401(self):
        check = require_scope("read")
        with pytest.raises(UnauthorizedError):
            check(authorization="frontend-key")

    def test_bearer_vacio_lanza_401(self):
        check = require_scope("read")
        with pytest.raises(UnauthorizedError):
            check(authorization="Bearer ")

    def test_api_key_no_registrada_lanza_401(self):
        check = require_scope("read")
        with pytest.raises(UnauthorizedError):
            check(authorization="Bearer no-existe")

    def test_key_valida_sin_el_scope_lanza_403(self):
        check = require_scope("write:matches")
        with pytest.raises(ForbiddenError):
            check(authorization="Bearer frontend-key")

    def test_key_valida_con_el_scope_no_lanza(self):
        check = require_scope("read")
        check(authorization="Bearer frontend-key")  # no debe lanzar

    def test_distintas_keys_tienen_distintos_scopes(self):
        require_scope("write:products")(authorization="Bearer panel-key")
        with pytest.raises(ForbiddenError):
            require_scope("write:products")(authorization="Bearer frontend-key")

"""Tests de tiendas -- docs/api-endpoints-v1.md sección 3 y
docs/api-endpoints-gestor.md sección 3.

El efecto REAL de `active` sobre el scraping (dispatcher.run_all_stores())
se prueba en store_monitor/tests/test_dispatcher.py -- aquí solo se prueba
que la API lee/escribe la columna correctamente."""

from __future__ import annotations

import pytest
from sqlalchemy import text

import services.stores as stores_service
from errors import NotFoundError
from schemas.stores import StorePatch


def seed_store(session, *, name="Cardzone", active=True, platform="shopify") -> int:
    row = session.exec(
        text("""
            INSERT INTO store (name, website_url, platform, active)
            VALUES (:name, :url, :platform, :active) RETURNING id
        """),
        params={"name": name, "url": f"https://{name.lower()}.example", "active": active, "platform": platform},
    ).first()
    session.commit()
    return row[0]


class TestListStores:
    def test_lista_incluye_active(self, session):
        seed_store(session, active=False)

        stores = stores_service.list_stores(session)

        assert stores[0].active is False

    def test_lista_incluye_tienda_generic_jsonld(self, session):
        # Regresión real (2026-08-27): StorePlatform (models/store.py) y su
        # PGEnum nunca incluyeron 'generic_jsonld' pese a que la BBDD sí lo
        # tiene desde que se añadió ese scraper -- cualquier listado que
        # tocara una tienda con esa plataforma (NIKOCHAN ARENA, ISEKAI,
        # Comic Stores) reventaba con LookupError/500, tanto en list_stores
        # como en list_stores_detailed (panel de administración).
        seed_store(session, name="NIKOCHAN ARENA", platform="generic_jsonld")

        stores = stores_service.list_stores(session)
        detailed = stores_service.list_stores_detailed(session)

        assert stores[0].platform == "generic_jsonld"
        assert detailed[0].platform == "generic_jsonld"


class TestGetStore:
    def test_404_si_no_existe(self, session):
        with pytest.raises(NotFoundError):
            stores_service.get_store(session, 999)

    def test_detalle_incluye_campos_dinamicos(self, session):
        store_id = seed_store(session)
        session.exec(text("UPDATE store SET consecutive_failures = 2 WHERE id = :id"), params={"id": store_id})
        session.commit()

        detail = stores_service.get_store(session, store_id)

        assert detail.consecutive_failures == 2
        assert detail.disallowed is False


class TestPatchStore:
    def test_patch_active_lo_persiste(self, session):
        store_id = seed_store(session, active=True)

        updated = stores_service.patch_store(session, store_id, StorePatch(active=False))

        assert updated.active is False
        assert stores_service.get_store(session, store_id).active is False

    def test_patch_solo_sitemap_url_no_toca_active(self, session):
        store_id = seed_store(session, active=True)

        updated = stores_service.patch_store(session, store_id, StorePatch(sitemap_url="https://x.example/sitemap.xml"))

        assert updated.active is True

    def test_404_si_no_existe(self, session):
        with pytest.raises(NotFoundError):
            stores_service.patch_store(session, 999, StorePatch(active=False))


class TestRouterStores:
    def test_get_stores_requiere_auth(self, client):
        assert client.get("/stores").status_code == 401

    def test_get_stores_con_auth(self, session, client, auth_headers):
        seed_store(session)

        body = client.get("/stores", headers=auth_headers).json()

        assert body["data"][0]["name"] == "Cardzone"

    def test_patch_requiere_scope_admin(self, client, seed_listing, auth_headers):
        seed_listing()
        resp = client.patch("/stores/1", headers=auth_headers, json={"active": False})
        assert resp.status_code == 403  # auth_headers solo trae scope "read"

    def test_patch_con_scope_admin_funciona(self, client, seed_listing, monkeypatch):
        import config
        monkeypatch.setattr(config, "API_KEYS", {"panel": frozenset({"read", "admin:*"})})
        seed_listing()

        resp = client.patch("/stores/1", headers={"Authorization": "Bearer panel"}, json={"active": False})

        assert resp.status_code == 200
        assert resp.json()["active"] is False

    def test_get_store_inexistente_404(self, client, auth_headers):
        resp = client.get("/stores/999", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.headers["content-type"] == "application/problem+json"

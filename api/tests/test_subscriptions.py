"""Tests de suscripciones de restock -- docs/api-endpoints-v1.md sección 5.

Sin Idempotency-Key (decidido 2026-08-27, ver docstring de
schemas/subscriptions.py): la idempotencia real la da el UNIQUE NULLS NOT
DISTINCT del esquema -- un reintento choca con él y devuelve 409."""

from __future__ import annotations

import pytest

import services.subscriptions as subscriptions_service
from errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from schemas.subscriptions import PushKeys, SubscriptionCreate


def make_create(product_id=1, store_id=None, push_endpoint="https://push.example/a"):
    return SubscriptionCreate(product_id=product_id, store_id=store_id, push_endpoint=push_endpoint,
                               push_keys=PushKeys(p256dh="p", auth="a"))


class TestCreateSubscription:
    def test_producto_inexistente_es_422(self, session):
        with pytest.raises(UnprocessableEntityError):
            subscriptions_service.create_subscription(session, make_create(product_id=999))

    def test_creacion_exitosa(self, session, seed_listing):
        product_id = seed_listing()

        result = subscriptions_service.create_subscription(session, make_create(product_id=product_id))

        assert result.product_id == product_id
        assert result.store_id is None

    def test_duplicado_exacto_es_409(self, session, seed_listing):
        product_id = seed_listing()
        subscriptions_service.create_subscription(session, make_create(product_id=product_id))

        with pytest.raises(ConflictError):
            subscriptions_service.create_subscription(session, make_create(product_id=product_id))

    def test_mismo_producto_distinto_push_endpoint_no_es_duplicado(self, session, seed_listing):
        product_id = seed_listing()
        subscriptions_service.create_subscription(
            session, make_create(product_id=product_id, push_endpoint="https://push.example/a"))

        result = subscriptions_service.create_subscription(
            session, make_create(product_id=product_id, push_endpoint="https://push.example/b"))

        assert result.id is not None


class TestDeleteSubscription:
    def test_404_si_no_existe(self, session):
        with pytest.raises(NotFoundError):
            subscriptions_service.delete_subscription(session, 999, "https://push.example/a")

    def test_403_si_push_endpoint_no_coincide(self, session, seed_listing):
        product_id = seed_listing()
        sub = subscriptions_service.create_subscription(session, make_create(product_id=product_id))

        with pytest.raises(ForbiddenError):
            subscriptions_service.delete_subscription(session, sub.id, "https://push.example/otro")

    def test_borrado_exitoso_con_el_pushendpoint_correcto(self, session, seed_listing):
        product_id = seed_listing()
        sub = subscriptions_service.create_subscription(session, make_create(product_id=product_id))

        subscriptions_service.delete_subscription(session, sub.id, "https://push.example/a")

        assert subscriptions_service.list_by_push_endpoint(session, "https://push.example/a") == []


class TestListByPushEndpoint:
    def test_lista_solo_las_de_ese_dispositivo(self, session, seed_listing):
        product_id = seed_listing()
        subscriptions_service.create_subscription(
            session, make_create(product_id=product_id, push_endpoint="https://push.example/mio"))
        subscriptions_service.create_subscription(
            session, make_create(product_id=product_id, push_endpoint="https://push.example/otro"))

        result = subscriptions_service.list_by_push_endpoint(session, "https://push.example/mio")

        assert len(result) == 1
        assert result[0].product_id == product_id


class TestRouterSubscriptions:
    def test_post_requiere_scope_write_subscriptions(self, client, seed_listing):
        product_id = seed_listing()
        body = {"productId": product_id, "storeId": None, "pushEndpoint": "https://push.example/x",
                "pushKeys": {"p256dh": "p", "auth": "a"}}
        resp = client.post("/subscriptions", json=body, headers={"Authorization": "Bearer sin-scope"})
        assert resp.status_code == 401  # key ni siquiera registrada

    def test_post_201_con_scope_correcto(self, client, seed_listing, monkeypatch):
        import config
        monkeypatch.setattr(config, "API_KEYS", {"front": frozenset({"read", "write:subscriptions"})})
        product_id = seed_listing()

        body = {"productId": product_id, "storeId": None, "pushEndpoint": "https://push.example/y",
                "pushKeys": {"p256dh": "p", "auth": "a"}}
        resp = client.post("/subscriptions", json=body, headers={"Authorization": "Bearer front"})

        assert resp.status_code == 201
        assert resp.json()["productId"] == product_id

    def test_delete_devuelve_204(self, client, seed_listing, monkeypatch):
        import config
        monkeypatch.setattr(config, "API_KEYS", {"front": frozenset({"read", "write:subscriptions"})})
        product_id = seed_listing()
        H = {"Authorization": "Bearer front"}

        body = {"productId": product_id, "storeId": None, "pushEndpoint": "https://push.example/z",
                "pushKeys": {"p256dh": "p", "auth": "a"}}
        created = client.post("/subscriptions", json=body, headers=H).json()

        resp = client.delete(f"/subscriptions/{created['id']}?pushEndpoint=https://push.example/z", headers=H)

        assert resp.status_code == 204

    def test_get_devuelve_las_del_pushendpoint(self, client, seed_listing, monkeypatch):
        import config
        monkeypatch.setattr(config, "API_KEYS", {"front": frozenset({"read", "write:subscriptions"})})
        H = {"Authorization": "Bearer front"}
        product_id = seed_listing()
        body = {"productId": product_id, "storeId": None, "pushEndpoint": "https://push.example/list",
                "pushKeys": {"p256dh": "p", "auth": "a"}}
        client.post("/subscriptions", json=body, headers=H)

        resp = client.get("/subscriptions?pushEndpoint=https://push.example/list", headers=H)

        assert resp.status_code == 200
        assert resp.json()["data"][0]["productId"] == product_id

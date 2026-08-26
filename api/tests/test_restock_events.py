"""Tests de GET /restock-events -- docs/api-endpoints-v1.md sección 2.1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import services.restock_events as restock_events_service
from schemas.restock_events import RestockEventFilters


def seed_restock_event(session, *, name="Booster Box OP16 EN", store_name="Cardzone",
                        price=109.90, hours_ago=1, game_slug="one-piece", category_slug="booster-box"):
    session.exec(text("INSERT INTO game (name, slug) VALUES (:s, :s) ON CONFLICT (slug) DO NOTHING"),
                 params={"s": game_slug})
    session.exec(text("INSERT INTO category (name, slug) VALUES (:s, :s) ON CONFLICT (slug) DO NOTHING"),
                 params={"s": category_slug})
    game_id = session.exec(text("SELECT id FROM game WHERE slug = :s"), params={"s": game_slug}).first()[0]
    category_id = session.exec(
        text("SELECT id FROM category WHERE slug = :s"), params={"s": category_slug},
    ).first()[0]

    product_id = session.exec(
        text("INSERT INTO product (game_id, category_id, name_canonical) VALUES (:g, :c, :n) RETURNING id"),
        params={"g": game_id, "c": category_id, "n": name},
    ).first()[0]
    store_id = session.exec(
        text("INSERT INTO store (name, website_url, platform) VALUES (:n, :u, 'shopify') RETURNING id"),
        params={"n": store_name, "u": f"https://{store_name.lower()}.example"},
    ).first()[0]
    store_product_id = session.exec(
        text("""
            INSERT INTO store_product (store_id, product_id, match_status, store_url, raw_name,
                                        current_price, stock_status)
            VALUES (:store_id, :product_id, 'confirmed', :url, :name, :price, 'disponible') RETURNING id
        """),
        params={"store_id": store_id, "product_id": product_id, "url": f"https://x.example/{product_id}",
                "name": name, "price": price},
    ).first()[0]
    detected_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    session.exec(
        text("""
            INSERT INTO restock_event (store_product_id, product_id, detected_at)
            VALUES (:sp, :p, :detected_at)
        """),
        params={"sp": store_product_id, "p": product_id, "detected_at": detected_at},
    )
    session.commit()


class TestListRecent:
    def test_evento_reciente_aparece(self, session):
        seed_restock_event(session, hours_ago=1)

        events = restock_events_service.list_recent(session, RestockEventFilters(hours=24))

        assert len(events) == 1
        assert events[0].store_name == "Cardzone"
        assert events[0].price == 109.90

    def test_evento_fuera_de_la_ventana_no_aparece(self, session):
        seed_restock_event(session, hours_ago=48)

        events = restock_events_service.list_recent(session, RestockEventFilters(hours=24))

        assert events == []

    def test_orden_descendente_por_detected_at(self, session):
        seed_restock_event(session, name="Viejo", hours_ago=5, store_name="TiendaVieja")
        seed_restock_event(session, name="Nuevo", hours_ago=1, store_name="TiendaNueva")

        events = restock_events_service.list_recent(session, RestockEventFilters(hours=24))

        assert [e.name_canonical for e in events] == ["Nuevo", "Viejo"]

    def test_filtro_por_game(self, session):
        seed_restock_event(session, game_slug="one-piece")

        events = restock_events_service.list_recent(session, RestockEventFilters(game="pokemon"))

        assert events == []

    def test_filtro_por_category(self, session):
        seed_restock_event(session, category_slug="booster-box")

        events = restock_events_service.list_recent(session, RestockEventFilters(category="no-existe"))

        assert events == []


class TestRouterRestockEvents:
    def test_requiere_auth(self, client):
        assert client.get("/restock-events").status_code == 401

    def test_con_auth_devuelve_camelcase(self, session, client, auth_headers):
        seed_restock_event(session)

        body = client.get("/restock-events", headers=auth_headers).json()

        assert "detectedAt" in body["data"][0]
        assert "nameCanonical" in body["data"][0]

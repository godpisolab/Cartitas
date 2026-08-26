"""Tests de GET /deals -- docs/api-endpoints-v1.md sección 2."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text

import services.deals as deals_service
from schemas.deals import COMPARISON_WINDOW_DAYS, DealFilters


def seed_deal_candidate(session, *, name="Booster Box OP16 EN", current_price=99.90, previous_price=119.90,
                         stock_status="disponible", days_ago=COMPARISON_WINDOW_DAYS, store_name="Cardzone"):
    session.exec(text("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece') "
                       "ON CONFLICT (slug) DO NOTHING"))
    session.exec(text("INSERT INTO category (name, slug) VALUES ('Booster Box', 'booster-box') "
                       "ON CONFLICT (slug) DO NOTHING"))
    game_id = session.exec(text("SELECT id FROM game WHERE slug = 'one-piece'")).first()[0]
    category_id = session.exec(text("SELECT id FROM category WHERE slug = 'booster-box'")).first()[0]

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
            VALUES (:store_id, :product_id, 'confirmed', :url, :name, :price, :stock_status) RETURNING id
        """),
        params={"store_id": store_id, "product_id": product_id, "url": f"https://x.example/{product_id}",
                "name": name, "price": current_price, "stock_status": stock_status},
    ).first()[0]
    if previous_price is not None:
        session.exec(
            text("""
                INSERT INTO price_history (store_product_id, price, stock_status, scraped_date)
                VALUES (:sp, :price, 'disponible', :scraped_date)
            """),
            params={"sp": store_product_id, "price": previous_price,
                    "scraped_date": date.today() - timedelta(days=days_ago)},
        )
    session.commit()
    return product_id


class TestSearchDeals:
    def test_bajada_de_precio_aparece_con_drop_percentage(self, session):
        seed_deal_candidate(session, current_price=99.90, previous_price=119.90)

        deals = deals_service.search_deals(session, DealFilters())

        assert len(deals) == 1
        assert deals[0].current_min_price == 99.90
        assert deals[0].previous_min_price == 119.90
        assert deals[0].drop_percentage > 0
        assert deals[0].store_name == "Cardzone"

    def test_subida_de_precio_no_aparece(self, session):
        seed_deal_candidate(session, current_price=150.0, previous_price=100.0)

        deals = deals_service.search_deals(session, DealFilters())

        assert deals == []

    def test_sin_historico_de_hace_exactamente_7_dias_no_entra(self, session):
        seed_deal_candidate(session, days_ago=3)  # no 7 días exactos

        deals = deals_service.search_deals(session, DealFilters())

        assert deals == []

    def test_agotado_no_entra_aunque_haya_bajado_de_precio(self, session):
        seed_deal_candidate(session, current_price=50.0, previous_price=100.0, stock_status="agotado")

        deals = deals_service.search_deals(session, DealFilters())

        assert deals == []

    def test_filtro_por_game(self, session):
        seed_deal_candidate(session, name="Booster Box OP16 EN")

        deals = deals_service.search_deals(session, DealFilters(game="pokemon"))

        assert deals == []

    def test_filtro_por_category(self, session):
        seed_deal_candidate(session, name="Booster Box OP16 EN")

        deals = deals_service.search_deals(session, DealFilters(category="no-existe"))

        assert deals == []

    def test_ordenado_por_mayor_caida_primero(self, session):
        seed_deal_candidate(session, name="Caida pequena", current_price=95.0, previous_price=100.0,
                             store_name="TiendaA")
        seed_deal_candidate(session, name="Caida grande", current_price=50.0, previous_price=100.0,
                             store_name="TiendaB")

        deals = deals_service.search_deals(session, DealFilters())

        assert [d.name_canonical for d in deals] == ["Caida grande", "Caida pequena"]


class TestRouterDeals:
    def test_requiere_auth(self, client):
        assert client.get("/deals").status_code == 401

    def test_con_auth_devuelve_camelcase(self, session, client, auth_headers):
        seed_deal_candidate(session)

        body = client.get("/deals", headers=auth_headers).json()

        assert "dropPercentage" in body["data"][0]
        assert "currentMinPrice" in body["data"][0]

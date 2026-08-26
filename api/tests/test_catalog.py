"""Tests de GET /games y GET /categories -- docs/api-endpoints-v1.md
sección 4."""

from __future__ import annotations

from sqlalchemy import text

import services.catalog as catalog_service


class TestListGames:
    def test_lista_juegos_sembrados(self, session):
        session.exec(text("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece')"))
        session.exec(text("INSERT INTO game (name, slug) VALUES ('Pokémon', 'pokemon')"))
        session.commit()

        games = catalog_service.list_games(session)

        assert {g.slug for g in games} == {"one-piece", "pokemon"}


class TestListCategories:
    def test_arbol_de_dos_niveles(self, session):
        session.exec(text("INSERT INTO category (name, slug) VALUES ('Sellado', 'sellado')"))
        session.exec(text(
            "INSERT INTO category (parent_category_id, name, slug) VALUES (1, 'Booster Box', 'booster-box')"
        ))
        session.exec(text(
            "INSERT INTO category (parent_category_id, name, slug) VALUES (1, 'Starter Deck', 'starter-deck')"
        ))
        session.commit()

        tree = catalog_service.list_categories(session)

        assert len(tree) == 1
        assert tree[0].slug == "sellado"
        assert {child.slug for child in tree[0].children} == {"booster-box", "starter-deck"}

    def test_categoria_sin_padre_ni_hijos_es_raiz_vacia(self, session):
        session.exec(text("INSERT INTO category (name, slug) VALUES ('Accesorios', 'accesorios')"))
        session.commit()

        tree = catalog_service.list_categories(session)

        assert tree[0].children == []


class TestRouterCatalog:
    def test_get_games_requiere_auth(self, client):
        assert client.get("/games").status_code == 401

    def test_get_games_devuelve_camelcase(self, session, client, auth_headers):
        session.exec(text("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece')"))
        session.commit()

        body = client.get("/games", headers=auth_headers).json()

        assert body["data"] == [{"id": 1, "name": "One Piece", "slug": "one-piece"}]

    def test_get_categories_requiere_auth(self, client):
        assert client.get("/categories").status_code == 401

    def test_get_categories_devuelve_camelcase(self, session, client, auth_headers):
        session.exec(text("INSERT INTO category (name, slug) VALUES ('Sellado', 'sellado')"))
        session.commit()

        body = client.get("/categories", headers=auth_headers).json()

        assert body["data"] == [{"id": 1, "name": "Sellado", "slug": "sellado", "children": []}]

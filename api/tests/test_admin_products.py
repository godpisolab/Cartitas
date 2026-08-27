"""Tests del panel de productos (HTML) -- listado, alta, edición. Mismo
patrón que test_admin_matches.py: TestClient + BBDD real, HTTP Basic
(docs/plan-cierre-panel-gestor.md sección 1.4)."""

from __future__ import annotations

from sqlalchemy import text


def seed_game(session, slug="one-piece"):
    session.exec(text("INSERT INTO game (name, slug) VALUES (:slug, :slug) ON CONFLICT (slug) DO NOTHING"),
                 params={"slug": slug})
    session.commit()
    return session.exec(text("SELECT id FROM game WHERE slug = :slug"), params={"slug": slug}).first()[0]


def seed_category(session, slug="booster-box"):
    session.exec(text("INSERT INTO category (name, slug) VALUES (:slug, :slug) ON CONFLICT (slug) DO NOTHING"),
                 params={"slug": slug})
    session.commit()
    return session.exec(text("SELECT id FROM category WHERE slug = :slug"), params={"slug": slug}).first()[0]


def seed_product(session, *, game_id=None, category_id=None,
                  name_canonical="Booster Box: The Time of Battle OP-16 EN",
                  main_set="OP16", language="EN") -> int:
    game_id = game_id or seed_game(session)
    category_id = category_id or seed_category(session)
    product_id = session.exec(
        text("""
            INSERT INTO product (game_id, category_id, main_set, language, name_canonical)
            VALUES (:g, :c, :m, :l, :n) RETURNING id
        """),
        params={"g": game_id, "c": category_id, "m": main_set, "l": language, "n": name_canonical},
    ).first()[0]
    session.commit()
    return product_id


def seed_confirmed_listing(session, product_id, *, store_name="Cardzone", price=109.90):
    store_id = session.exec(
        text("INSERT INTO store (name, website_url, platform) VALUES (:n, :u, 'shopify') RETURNING id"),
        params={"n": store_name, "u": f"https://{store_name.lower()}.example"},
    ).first()[0]
    session.exec(
        text("""
            INSERT INTO store_product (store_id, product_id, match_status, store_url, raw_name,
                                        current_price, stock_status)
            VALUES (:store_id, :product_id, 'confirmed', :url, 'nombre en tienda', :price, 'disponible')
        """),
        params={"store_id": store_id, "product_id": product_id,
                "url": f"https://{store_name.lower()}.example/p{product_id}", "price": price},
    )
    session.commit()


class TestAdminProductsAuth:
    def test_listado_sin_credenciales_es_401(self, client):
        assert client.get("/admin/products").status_code == 401

    def test_formulario_alta_sin_credenciales_es_401(self, client):
        assert client.get("/admin/products/new").status_code == 401

    def test_crear_sin_admin_configurado_es_401(self, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "ADMIN_USERNAME", "")
        monkeypatch.setattr(config, "ADMIN_PASSWORD", "")

        resp = client.post(
            "/admin/products",
            data={"game_id": 1, "category_id": 1, "name_canonical": "x"},
            auth=("", ""),
        )

        assert resp.status_code == 401

    def test_editar_sin_admin_configurado_es_401(self, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "ADMIN_USERNAME", "")
        monkeypatch.setattr(config, "ADMIN_PASSWORD", "")

        resp = client.post("/admin/products/1", data={"name_canonical": "x"}, auth=("", ""))

        assert resp.status_code == 401


class TestAdminProductsList:
    def test_lista_productos_con_listing_confirmado(self, session, client, admin_credentials):
        product_id = seed_product(session)
        seed_confirmed_listing(session, product_id)

        resp = client.get("/admin/products", auth=admin_credentials)

        assert resp.status_code == 200
        assert "Booster Box: The Time of Battle OP-16 EN" in resp.text

    def test_busqueda_por_q_filtra(self, session, client, admin_credentials):
        producto_a = seed_product(session, name_canonical="Starter Deck Luffy ST-01 EN")
        producto_b = seed_product(session, name_canonical="Booster Box: The Time of Battle OP-16 EN")
        seed_confirmed_listing(session, producto_a, store_name="TiendaA")
        seed_confirmed_listing(session, producto_b, store_name="TiendaB")

        resp = client.get("/admin/products?q=Starter Deck Luffy", auth=admin_credentials)

        assert resp.status_code == 200
        assert "Starter Deck Luffy ST-01 EN" in resp.text


class TestAdminProductsNew:
    def test_formulario_alta_preellenado_desde_query_params(self, client, admin_credentials):
        resp = client.get(
            "/admin/products/new?productType=BOOSTER_BOX&mainSet=OP17&language=EN", auth=admin_credentials,
        )

        assert resp.status_code == 200
        assert 'value="OP17"' in resp.text
        assert 'value="EN" selected' in resp.text


class TestAdminProductsCreate:
    def test_alta_producto_devuelve_redirect_a_la_ficha_de_edicion(self, session, client, admin_credentials):
        game_id = seed_game(session)
        category_id = seed_category(session)

        resp = client.post(
            "/admin/products",
            data={"game_id": game_id, "category_id": category_id, "name_canonical": "Producto nuevo EN"},
            auth=admin_credentials,
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/admin/products/")
        assert resp.headers["location"].endswith("/edit")

    def test_alta_producto_duplicado_muestra_error_en_formulario(self, session, client, admin_credentials):
        game_id = seed_game(session)
        category_id = seed_category(session)
        seed_product(session, game_id=game_id, category_id=category_id, name_canonical="Ya existe EN")

        resp = client.post(
            "/admin/products",
            data={"game_id": game_id, "category_id": category_id, "name_canonical": "Ya existe EN"},
            auth=admin_credentials,
        )

        assert resp.status_code == 409
        # No pierde lo ya escrito por la persona.
        assert 'value="Ya existe EN"' in resp.text


class TestAdminProductsEdit:
    def test_editar_producto_inexistente_es_404(self, client, admin_credentials):
        resp = client.get("/admin/products/999999/edit", auth=admin_credentials)
        assert resp.status_code == 404

    def test_producto_sin_listings_confirmados_es_editable(self, session, client, admin_credentials):
        product_id = seed_product(session)  # sin ningún store_product confirmado

        resp = client.get(f"/admin/products/{product_id}/edit", auth=admin_credentials)

        assert resp.status_code == 200

    def test_editar_marca_is_hot_correctamente(self, session, client, admin_credentials):
        product_id = seed_product(session)

        resp = client.post(
            f"/admin/products/{product_id}",
            data={"name_canonical": "Booster Box: The Time of Battle OP-16 EN", "is_hot": "true"},
            auth=admin_credentials,
            follow_redirects=False,
        )

        assert resp.status_code == 303
        is_hot = session.exec(text("SELECT is_hot FROM product WHERE id = :id"), params={"id": product_id}).first()[0]
        assert is_hot is True

    def test_editar_sin_is_hot_lo_desmarca(self, session, client, admin_credentials):
        product_id = seed_product(session)
        session.exec(text("UPDATE product SET is_hot = true WHERE id = :id"), params={"id": product_id})
        session.commit()

        client.post(
            f"/admin/products/{product_id}",
            data={"name_canonical": "Booster Box: The Time of Battle OP-16 EN"},  # sin is_hot -- checkbox sin marcar
            auth=admin_credentials,
            follow_redirects=False,
        )

        is_hot = session.exec(text("SELECT is_hot FROM product WHERE id = :id"), params={"id": product_id}).first()[0]
        assert is_hot is False

    def test_editar_a_nombre_duplicado_muestra_error_sin_perder_lo_escrito(self, session, client, admin_credentials):
        game_id = seed_game(session)
        category_id = seed_category(session)
        seed_product(session, game_id=game_id, category_id=category_id, name_canonical="Ya existe EN")
        product_id = seed_product(session, game_id=game_id, category_id=category_id,
                                   name_canonical="Producto a renombrar EN")

        resp = client.post(
            f"/admin/products/{product_id}",
            data={"name_canonical": "Ya existe EN"},
            auth=admin_credentials,
        )

        assert resp.status_code == 409
        assert 'value="Ya existe EN"' in resp.text
        name = session.exec(
            text("SELECT name_canonical FROM product WHERE id = :id"), params={"id": product_id},
        ).first()[0]
        assert name == "Producto a renombrar EN"

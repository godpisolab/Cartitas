"""Tests del panel de gestor (HTML/htmx) -- extensión directa de
`test_matches.py` sobre `admin/routes/matches.py`, mismo `TestClient` +
BBDD real, credenciales de HTTP Basic en vez de Bearer
(docs/estandares-implementacion-frontend.md sección 2.6)."""

from __future__ import annotations

from sqlalchemy import text


def seed_category(session, slug="booster-box"):
    row = session.exec(
        text("INSERT INTO category (name, slug) VALUES (:slug, :slug) RETURNING id"), params={"slug": slug},
    ).first()
    session.commit()
    return row[0]


def seed_store_product(session, *, raw_name="One Piece TCG OP16 Booster Box (EN)", store_name="Cardzone",
                        match_status="needs_review", product_id=None) -> int:
    store_id = session.exec(
        text("INSERT INTO store (name, website_url, platform) VALUES (:n, :u, 'shopify') RETURNING id"),
        params={"n": store_name, "u": f"https://{store_name.lower()}.example"},
    ).first()[0]
    sp_id = session.exec(
        text("""
            INSERT INTO store_product (store_id, product_id, match_status, store_url, raw_name,
                                        current_price, stock_status)
            VALUES (:store_id, :product_id, :status, :url, :raw_name, 119.90, 'disponible')
            RETURNING id
        """),
        params={"store_id": store_id, "product_id": product_id, "status": match_status,
                "url": f"https://x.example/{store_id}", "raw_name": raw_name},
    ).first()[0]
    session.commit()
    return sp_id


def seed_product(session, *, name_canonical="Booster Box: The Time of Battle OP-16 EN", category_id=None,
                  main_set="OP16", language="EN") -> int:
    session.exec(text("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece') "
                       "ON CONFLICT (slug) DO NOTHING"))
    game_id = session.exec(text("SELECT id FROM game WHERE slug = 'one-piece'")).first()[0]
    if category_id is None:
        category_id = seed_category(session)
    product_id = session.exec(
        text("""
            INSERT INTO product (game_id, category_id, main_set, language, name_canonical)
            VALUES (:g, :c, :m, :l, :n) RETURNING id
        """),
        params={"g": game_id, "c": category_id, "m": main_set, "l": language, "n": name_canonical},
    ).first()[0]
    session.commit()
    return product_id


class TestAdminAuth:
    def test_sin_credenciales_es_401(self, client):
        resp = client.get("/admin/matches")
        assert resp.status_code == 401

    def test_credenciales_incorrectas_es_401(self, client, admin_credentials):
        resp = client.get("/admin/matches", auth=("admin", "wrong-password"))
        assert resp.status_code == 401

    def test_credenciales_correctas_pasa(self, client, admin_credentials):
        resp = client.get("/admin/matches", auth=admin_credentials)
        assert resp.status_code == 200

    def test_sin_admin_configurado_credenciales_vacias_no_pasan(self, client, monkeypatch):
        """Simula el despliegue donde nadie fijó ADMIN_USERNAME/ADMIN_PASSWORD
        -- ambas quedan "" (default de config.py). Sin el check explícito de
        cadena vacía en verify_admin, compare_digest("", "") sería True y
        unas credenciales vacías (curl -u ":") entrarían como admin."""
        import config
        monkeypatch.setattr(config, "ADMIN_USERNAME", "")
        monkeypatch.setattr(config, "ADMIN_PASSWORD", "")

        resp = client.get("/admin/matches", auth=("", ""))

        assert resp.status_code == 401

    def test_sin_admin_configurado_escritura_tambien_bloqueada(self, session, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "ADMIN_USERNAME", "")
        monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
        sp_id = seed_store_product(session)

        resp = client.post(f"/admin/matches/{sp_id}/reject", data={"mark_as": "unmatched"}, auth=("", ""))

        assert resp.status_code == 401


class TestAdminListMatches:
    def test_lista_por_defecto_needs_review(self, session, client, admin_credentials):
        seed_product(session)
        seed_store_product(session, match_status="needs_review")
        seed_store_product(session, match_status="unmatched", store_name="Otra")

        resp = client.get("/admin/matches", auth=admin_credentials)

        assert resp.status_code == 200
        assert "Cardzone" in resp.text
        assert "Otra" not in resp.text

    def test_status_via_query_param(self, session, client, admin_credentials):
        seed_store_product(session, match_status="unmatched", store_name="Otra")

        resp = client.get("/admin/matches?status=unmatched", auth=admin_credentials)

        assert resp.status_code == 200
        assert "Otra" in resp.text

    def test_sin_elementos_muestra_mensaje_vacio(self, client, admin_credentials):
        resp = client.get("/admin/matches", auth=admin_credentials)

        assert resp.status_code == 200
        assert "No hay elementos pendientes de revisión" in resp.text


class TestAdminConfirmRejectReopen:
    def test_confirmar_devuelve_fragmento_de_fila_actualizado(self, session, client, admin_credentials):
        product_id = seed_product(session)
        sp_id = seed_store_product(session)

        resp = client.post(
            f"/admin/matches/{sp_id}/confirm", data={"product_id": product_id}, auth=admin_credentials,
        )

        assert resp.status_code == 200
        assert f"Producto #{product_id}" in resp.text
        assert f'id="match-{sp_id}"' in resp.text

    def test_rechazar_devuelve_fragmento_de_fila_actualizado(self, session, client, admin_credentials):
        sp_id = seed_store_product(session)

        resp = client.post(
            f"/admin/matches/{sp_id}/reject", data={"mark_as": "unmatched"}, auth=admin_credentials,
        )

        assert resp.status_code == 200
        assert "unmatched" in resp.text

    def test_reabrir_un_confirmado_devuelve_fragmento_con_candidatos(self, session, client, admin_credentials):
        product_id = seed_product(session)
        sp_id = seed_store_product(session, match_status="confirmed", product_id=product_id)

        resp = client.post(f"/admin/matches/{sp_id}/reopen", auth=admin_credentials)

        assert resp.status_code == 200
        assert "needs_review" in resp.text

    def test_acciones_requieren_credenciales(self, client):
        resp = client.post("/admin/matches/1/reject", data={"mark_as": "unmatched"})
        assert resp.status_code == 401

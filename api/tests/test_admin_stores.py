"""Tests del panel de tiendas (HTML) -- listado con columna de salud,
detalle + edición de sitemapUrl/active (docs/plan-cierre-panel-gestor.md
sección 1.5)."""

from __future__ import annotations

from sqlalchemy import text


def seed_store(session, *, name="Cardzone", active=True, consecutive_failures=0, sitemap_url=None) -> int:
    row = session.exec(
        text("""
            INSERT INTO store (name, website_url, platform, active, consecutive_failures, sitemap_url)
            VALUES (:name, :url, 'shopify', :active, :failures, :sitemap_url) RETURNING id
        """),
        params={"name": name, "url": f"https://{name.lower()}.example", "active": active,
                "failures": consecutive_failures, "sitemap_url": sitemap_url},
    ).first()
    session.commit()
    return row[0]


class TestAdminStoresAuth:
    def test_listado_sin_credenciales_es_401(self, client):
        assert client.get("/admin/stores").status_code == 401

    def test_editar_sin_admin_configurado_es_401(self, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "ADMIN_USERNAME", "")
        monkeypatch.setattr(config, "ADMIN_PASSWORD", "")

        resp = client.post("/admin/stores/1", data={"sitemap_url": "https://x.example/sitemap.xml"}, auth=("", ""))

        assert resp.status_code == 401


class TestAdminStoresList:
    def test_listado_tiendas_muestra_columna_salud(self, session, client, admin_credentials):
        seed_store(session, name="TiendaConFallos", consecutive_failures=3)

        resp = client.get("/admin/stores", auth=admin_credentials)

        assert resp.status_code == 200
        assert "TiendaConFallos" in resp.text
        assert "<td>3</td>" in resp.text


class TestAdminStoresDetail:
    def test_editar_sitemap_url_se_refleja_en_bbdd(self, session, client, admin_credentials):
        store_id = seed_store(session)

        resp = client.post(
            f"/admin/stores/{store_id}",
            data={"sitemap_url": "https://cardzone.example/sitemap.xml", "active": "true"},
            auth=admin_credentials,
            follow_redirects=False,
        )

        assert resp.status_code == 303
        row = session.exec(text("SELECT sitemap_url FROM store WHERE id = :id"), params={"id": store_id}).first()
        assert row[0] == "https://cardzone.example/sitemap.xml"

    def test_desactivar_tienda_pone_active_false(self, session, client, admin_credentials):
        store_id = seed_store(session, active=True)

        client.post(
            f"/admin/stores/{store_id}",
            data={},  # sin "active" -- checkbox sin marcar
            auth=admin_credentials,
            follow_redirects=False,
        )

        row = session.exec(text("SELECT active FROM store WHERE id = :id"), params={"id": store_id}).first()
        assert row[0] is False

    def test_detalle_muestra_valor_actual_de_sitemap_url(self, session, client, admin_credentials):
        store_id = seed_store(session, sitemap_url="https://cardzone.example/sitemap.xml")

        resp = client.get(f"/admin/stores/{store_id}", auth=admin_credentials)

        assert resp.status_code == 200
        assert 'value="https://cardzone.example/sitemap.xml"' in resp.text

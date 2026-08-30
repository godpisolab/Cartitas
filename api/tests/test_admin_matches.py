"""Tests del panel de gestor (HTML/htmx) -- extensión directa de
`test_matches.py` sobre `admin/routes/matches.py`, mismo `TestClient` +
BBDD real, credenciales de HTTP Basic en vez de Bearer
(docs/estandares-implementacion-frontend.md sección 2.6)."""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import text

import services.jobs as jobs_service
from errors import BadGatewayError


def seed_category(session, slug="one-piece"):
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
                  main_set="OP16", language="EN", set_code=None, packaging=None) -> int:
    session.exec(text("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece') "
                       "ON CONFLICT (slug) DO NOTHING"))
    game_id = session.exec(text("SELECT id FROM game WHERE slug = 'one-piece'")).first()[0]
    if category_id is None:
        category_id = seed_category(session)
    product_id = session.exec(
        text("""
            INSERT INTO product (game_id, category_id, main_set, language, name_canonical, set_code, packaging)
            VALUES (:g, :c, :m, :l, :n, :sc, :pkg) RETURNING id
        """),
        params={"g": game_id, "c": category_id, "m": main_set, "l": language, "n": name_canonical,
                "sc": set_code, "pkg": packaging},
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
        seed_product(session, set_code="OP16", packaging="display")
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

    def test_filtro_status_confirmed_muestra_solo_confirmados(self, session, client, admin_credentials):
        product_id = seed_product(session)
        seed_store_product(session, match_status="needs_review", store_name="TiendaSinRevisar")
        seed_store_product(session, match_status="confirmed", product_id=product_id, store_name="TiendaConfirmada")

        resp = client.get("/admin/matches?status=confirmed", auth=admin_credentials)

        assert resp.status_code == 200
        assert "TiendaConfirmada" in resp.text
        assert "TiendaSinRevisar" not in resp.text

    def test_enlaces_de_filtro_presentes_en_cada_valor(self, client, admin_credentials):
        resp = client.get("/admin/matches", auth=admin_credentials)

        assert resp.status_code == 200
        for value in ("needsReview", "unmatched", "confirmed", "all"):
            assert f"/admin/matches?status={value}" in resp.text

    def test_enlace_del_filtro_activo_marca_la_clase_active(self, client, admin_credentials):
        resp = client.get("/admin/matches?status=confirmed", auth=admin_credentials)

        assert resp.status_code == 200
        assert 'href="/admin/matches?status=confirmed" class="active"' in resp.text

    def test_con_51_filas_pagina_2_llega_a_la_ultima(self, session, client, admin_credentials):
        # Hallazgo real (2026-08-28): con 187 needs_review reales, el panel
        # solo pedía page=1 (limit=50 por defecto) sin exponer ?page= ni
        # ningún enlace de paginación -- las otras 137 filas eran
        # invisibles desde el navegador.
        seed_product(session, set_code="OP16", packaging="display")
        for i in range(51):
            seed_store_product(session, store_name=f"Tienda{i}")

        pagina_1 = client.get("/admin/matches", auth=admin_credentials)
        pagina_2 = client.get("/admin/matches?page=2", auth=admin_credentials)

        assert pagina_1.status_code == 200
        assert pagina_1.text.count("<tr") - 1 == 50  # -1 por la fila de cabecera <thead><tr>
        assert "Página 1 de 2" in pagina_1.text
        assert "/admin/matches?status=needsReview&page=2" in pagina_1.text  # enlace "Siguiente"

        assert pagina_2.status_code == 200
        assert pagina_2.text.count("<tr") - 1 == 1
        assert "Página 2 de 2" in pagina_2.text
        assert "/admin/matches?status=needsReview&page=1" in pagina_2.text  # enlace "Anterior"

    def test_con_menos_de_50_filas_no_muestra_paginacion(self, session, client, admin_credentials):
        seed_store_product(session)

        resp = client.get("/admin/matches", auth=admin_credentials)

        assert resp.status_code == 200
        assert "Página" not in resp.text


class TestAdminRunMatching:
    """Botón "Relanzar matching" -- delega en services/jobs.py (HTTP hacia
    store_monitor/jobs_api.py, ver docstring de esa función), mockeado aquí
    igual que test_admin_jobs.py hace con los triggers de scraping."""

    def test_sin_credenciales_es_401(self, client):
        assert client.post("/admin/matches/run-matching").status_code == 401

    def test_ok_muestra_el_resumen_y_refresca_la_tabla(self, session, client, admin_credentials, monkeypatch):
        seed_product(session)
        seed_store_product(session, match_status="needs_review")
        monkeypatch.setattr(jobs_service, "trigger_run_matching", MagicMock(
            return_value={"confirmed": 1, "needs_review": 2}
        ))

        resp = client.post("/admin/matches/run-matching", auth=admin_credentials)

        assert resp.status_code == 200
        assert "Matching relanzado" in resp.text
        assert "1 confirmed" in resp.text
        assert "2 needs_review" in resp.text
        assert 'id="matches-wrapper"' in resp.text

    def test_respeta_el_filtro_de_status_actual(self, session, client, admin_credentials, monkeypatch):
        seed_product(session)
        seed_store_product(session, match_status="unmatched")
        monkeypatch.setattr(jobs_service, "trigger_run_matching", MagicMock(return_value={"unmatched": 1}))

        resp = client.post("/admin/matches/run-matching?status=unmatched", auth=admin_credentials)

        assert resp.status_code == 200
        assert "unmatched" in resp.text.lower()

    def test_servicio_de_jobs_caido_muestra_mensaje_en_vez_de_reventar(self, client, admin_credentials, monkeypatch):
        # Mismo patrón que TestServicioInternoCaido en test_admin_jobs.py:
        # 200 a propósito -- htmx no hace swap de respuestas de error.
        monkeypatch.setattr(jobs_service, "trigger_run_matching", MagicMock(
            side_effect=BadGatewayError("no se pudo contactar con el servicio de jobs")
        ))

        resp = client.post("/admin/matches/run-matching", auth=admin_credentials)

        assert resp.status_code == 200
        assert "no se pudo contactar" in resp.text


class TestAdminRejectForm:
    def test_fila_pendiente_muestra_formulario_con_select_y_reason(self, session, client, admin_credentials):
        seed_store_product(session, match_status="needs_review")

        resp = client.get("/admin/matches", auth=admin_credentials)

        assert resp.status_code == 200
        assert 'name="mark_as"' in resp.text
        assert '<option value="unmatched">' in resp.text
        assert '<option value="needsReview">' in resp.text
        assert 'name="reason"' in resp.text


class TestAdminMissingCandidates:
    def test_lista_agrupaciones_reales(self, session, client, admin_credentials):
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaA")
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaB")

        resp = client.get("/admin/missing-candidates", auth=admin_credentials)

        assert resp.status_code == 200
        assert "ONE_PIECE" in resp.text
        assert "<td>2</td>" in resp.text

    def test_respeta_min_stores(self, session, client, admin_credentials):
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaUnica")

        resp = client.get("/admin/missing-candidates?minStores=2", auth=admin_credentials)

        assert resp.status_code == 200
        assert "No hay combinaciones sin candidato" in resp.text

    def test_enlace_crear_canonico_preellena_el_formulario(self, session, client, admin_credentials):
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaA")
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaB")

        resp = client.get("/admin/missing-candidates", auth=admin_credentials)

        assert resp.status_code == 200
        assert "/admin/products/new?productType=ONE_PIECE&setCode=OP17&mainSet=OP17&language=EN&packaging=display" \
            in resp.text

    def test_requiere_credenciales(self, client):
        resp = client.get("/admin/missing-candidates")
        assert resp.status_code == 401


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

    def test_rechazar_con_needs_review_deja_ese_estado(self, session, client, admin_credentials):
        sp_id = seed_store_product(session, match_status="unmatched")

        resp = client.post(
            f"/admin/matches/{sp_id}/reject", data={"mark_as": "needsReview"}, auth=admin_credentials,
        )

        assert resp.status_code == 200
        assert "needs_review" in resp.text

    def test_rechazar_con_reason_lo_persiste_y_lo_muestra(self, session, client, admin_credentials):
        sp_id = seed_store_product(session)

        resp = client.post(
            f"/admin/matches/{sp_id}/reject",
            data={"mark_as": "unmatched", "reason": "Es un accesorio, no una caja"},
            auth=admin_credentials,
        )

        assert resp.status_code == 200
        assert "Es un accesorio, no una caja" in resp.text

    def test_reabrir_un_confirmado_devuelve_fragmento_con_candidatos(self, session, client, admin_credentials):
        product_id = seed_product(session)
        sp_id = seed_store_product(session, match_status="confirmed", product_id=product_id)

        resp = client.post(f"/admin/matches/{sp_id}/reopen", auth=admin_credentials)

        assert resp.status_code == 200
        assert "needs_review" in resp.text

    def test_acciones_requieren_credenciales(self, client):
        resp = client.post("/admin/matches/1/reject", data={"mark_as": "unmatched"})
        assert resp.status_code == 401

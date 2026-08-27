"""Tests del panel de matching -- docs/api-endpoints-v1.md sección 6 y
docs/api-endpoints-gestor.md sección 1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import services.matches as matches_service
from errors import ConflictError, NotFoundError, UnprocessableEntityError
from schemas.matches import ConfirmBody, MatchFilters, RejectBody


def seed_category(session, slug="booster-box"):
    row = session.exec(
        text("INSERT INTO category (name, slug) VALUES (:slug, :slug) RETURNING id"), params={"slug": slug},
    ).first()
    session.commit()
    return row[0]


def seed_store_product(session, *, raw_name="One Piece TCG OP16 Booster Box (EN)", store_name="Cardzone",
                        match_status="needs_review", product_id=None, reviewed_at=None) -> int:
    store_id = session.exec(
        text("INSERT INTO store (name, website_url, platform) VALUES (:n, :u, 'shopify') RETURNING id"),
        params={"n": store_name, "u": f"https://{store_name.lower()}.example"},
    ).first()[0]
    sp_id = session.exec(
        text("""
            INSERT INTO store_product (store_id, product_id, match_status, store_url, raw_name,
                                        current_price, stock_status, reviewed_at)
            VALUES (:store_id, :product_id, :status, :url, :raw_name, 119.90, 'disponible', :reviewed_at)
            RETURNING id
        """),
        params={"store_id": store_id, "product_id": product_id, "status": match_status,
                "url": f"https://x.example/{store_id}", "raw_name": raw_name, "reviewed_at": reviewed_at},
    ).first()[0]
    session.commit()
    return sp_id


def seed_product(session, *, name_canonical="Booster Box: The Time of Battle OP-16 EN", category_id=None,
                  main_set="OP16", language="EN", set_code=None) -> int:
    session.exec(text("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece') "
                       "ON CONFLICT (slug) DO NOTHING"))
    game_id = session.exec(text("SELECT id FROM game WHERE slug = 'one-piece'")).first()[0]
    if category_id is None:
        category_id = seed_category(session)
    product_id = session.exec(
        text("""
            INSERT INTO product (game_id, category_id, main_set, language, name_canonical, set_code)
            VALUES (:g, :c, :m, :l, :n, :sc) RETURNING id
        """),
        params={"g": game_id, "c": category_id, "m": main_set, "l": language, "n": name_canonical,
                "sc": set_code},
    ).first()[0]
    session.commit()
    return product_id, category_id


class TestListMatchesStatusFilter:
    def test_default_status_es_needs_review(self, session):
        _, category_id = seed_product(session)
        seed_store_product(session, match_status="needs_review")
        seed_store_product(session, match_status="unmatched", store_name="Otra")

        result = matches_service.list_matches(session, MatchFilters())

        assert len(result.data) == 1
        assert result.data[0].match_status.value == "needs_review"

    def test_not_applicable_nunca_aparece_ni_con_all(self, session):
        seed_store_product(session, match_status="not_applicable")

        result = matches_service.list_matches(session, MatchFilters(status="all"))

        assert result.data == []

    def test_status_confirmed_es_un_valor_valido(self, session):
        product_id, _ = seed_product(session)
        seed_store_product(session, match_status="confirmed", product_id=product_id)

        result = matches_service.list_matches(session, MatchFilters(status="confirmed"))

        assert len(result.data) == 1
        assert result.data[0].product_id == product_id


class TestConfirmedItemsShape:
    def test_confirmed_trae_product_id_y_candidates_vacio(self, session):
        product_id, _ = seed_product(session)
        seed_store_product(session, match_status="confirmed", product_id=product_id)

        result = matches_service.list_matches(session, MatchFilters(status="confirmed"))

        item = result.data[0]
        assert item.candidates == []
        assert item.product_id == product_id

    def test_needs_review_trae_candidatos_y_product_id_none(self, session):
        seed_product(session)  # candidato plausible en la misma categoría
        seed_store_product(session, match_status="needs_review")

        result = matches_service.list_matches(session, MatchFilters())

        item = result.data[0]
        assert item.product_id is None
        assert len(item.candidates) >= 1


class TestFiltroStoreId:
    def test_filtra_por_store_id(self, session):
        seed_product(session)
        sp_a = seed_store_product(session, store_name="TiendaA")
        seed_store_product(session, store_name="TiendaB")

        # store_id de sp_a
        store_id = session.exec(
            text("SELECT store_id FROM store_product WHERE id = :id"), params={"id": sp_a},
        ).first()[0]

        result = matches_service.list_matches(session, MatchFilters(store_id=store_id))

        assert len(result.data) == 1
        assert result.data[0].store_product_id == sp_a


class TestFiltroReviewedAt:
    def test_reviewed_reciente_se_oculta_por_defecto(self, session):
        seed_product(session)
        seed_store_product(session, reviewed_at=datetime.now(timezone.utc) - timedelta(days=1))

        result = matches_service.list_matches(session, MatchFilters())

        assert result.data == []

    def test_reviewed_reciente_aparece_con_include_reviewed(self, session):
        seed_product(session)
        seed_store_product(session, reviewed_at=datetime.now(timezone.utc) - timedelta(days=1))

        result = matches_service.list_matches(session, MatchFilters(include_reviewed=True))

        assert len(result.data) == 1

    def test_reviewed_hace_mas_de_14_dias_vuelve_a_aparecer(self, session):
        seed_product(session)
        seed_store_product(session, reviewed_at=datetime.now(timezone.utc) - timedelta(days=15))

        result = matches_service.list_matches(session, MatchFilters())

        assert len(result.data) == 1


class TestRawNameSinCategoriaMapeada:
    def test_producto_type_otros_no_tiene_candidatos_pero_no_revienta(self, session):
        # "Funda protectora genérica" no matchea ningún keyword de
        # CLASSIFICATION_RULES -- classify_product() la clasifica como
        # OTROS, que no está en PRODUCT_TYPE_TO_CATEGORY_SLUG.
        seed_store_product(session, raw_name="Funda protectora genérica")

        result = matches_service.list_matches(session, MatchFilters())

        assert len(result.data) == 1
        assert result.data[0].candidates == []


class TestFiltroSimilarity:
    def test_min_similarity_excluye_candidatos_debiles(self, session):
        seed_product(session, name_canonical="Algo completamente distinto sin relacion")
        seed_store_product(session, raw_name="One Piece TCG OP16 Booster Box (EN)")

        result = matches_service.list_matches(session, MatchFilters(min_similarity=0.9))

        assert result.data == []

    def test_max_similarity_excluye_candidatos_demasiado_fuertes(self, session):
        seed_product(session, name_canonical="Booster Box: The Time of Battle OP-16 EN")
        seed_store_product(session, raw_name="Booster Box: The Time of Battle OP-16 EN")

        result = matches_service.list_matches(session, MatchFilters(max_similarity=0.5))

        assert result.data == []


class TestCandidatosPriorizanSetCodeExacto:
    """Revisión de la cola de matching (2026-08-27): un candidato genérico
    reutilizado como plantilla en muchos nombres (ej. "Starter Deck ONE
    PIECE FILM edition ST-05") puede ganar por similitud de texto pura al
    candidato realmente correcto de otro código -- set_code debe desempatar
    a favor del código que trae el raw_name, no dejarlo enterrado en el
    puesto #2/#3."""

    def test_candidato_de_set_code_exacto_sale_primero_pese_a_menos_similitud(self, session):
        category_id = seed_category(session, slug="starter-deck")
        _, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Starter Deck ONE PIECE FILM edition ST-05 EN", set_code="ST05",
        )
        correcto_id, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Starter Deck: Red Monkey.D.Luffy ST-31 EN", set_code="ST31",
        )
        seed_store_product(session, raw_name="LUFFY – STARTER DECK ONE PIECE – ST 31")

        result = matches_service.list_matches(session, MatchFilters())

        assert result.data[0].candidates[0].product_id == correcto_id

    def test_sin_set_code_reconocible_se_queda_con_similitud_pura(self, session):
        category_id = seed_category(session, slug="starter-deck")
        mas_parecido_id, _ = seed_product(
            session, category_id=category_id, name_canonical="Starter Deck ONE PIECE FILM edition EN",
        )
        seed_product(session, category_id=category_id,
                     name_canonical="Mazo de inicio con un texto completamente distinto")
        # Sin dígitos -- classify_product() no extrae ningún set_code de
        # este raw_name, así que el desempate por código no aplica y manda
        # la similitud de texto de siempre (comparte "Starter Deck ONE
        # PIECE FILM" con el primer candidato, nada con el segundo).
        seed_store_product(session, raw_name="Starter Deck ONE PIECE FILM version")

        result = matches_service.list_matches(session, MatchFilters())

        assert result.data[0].candidates[0].product_id == mas_parecido_id


class TestCandidatosPriorizanCajaVsSobre:
    """Caso real encontrado revisando la cola (2026-08-27): "Premium
    Booster" (sobre) y "Premium Booster Box" (caja) del mismo PRB-02
    conviven en la MISMA categoría con el MISMO set_code -- ese desempate
    no distingue cuál es cuál, hace falta is_box_variant()."""

    def test_caja_prioriza_el_candidato_caja_mismo_set_code(self, session):
        category_id = seed_category(session, slug="premium-collection")
        sobre_id, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Premium Booster: One Piece Card The Best vol.2 PRB-02 EN", set_code="PRB02",
        )
        caja_id, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", set_code="PRB02",
        )
        seed_store_product(session, raw_name="CAJA THE BEST VOL.2 – PRB-02 – ONE PIECE")

        result = matches_service.list_matches(session, MatchFilters())

        assert result.data[0].candidates[0].product_id == caja_id
        assert result.data[0].candidates[0].product_id != sobre_id

    def test_sobre_prioriza_el_candidato_sobre_mismo_set_code(self, session):
        category_id = seed_category(session, slug="premium-collection")
        sobre_id, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Premium Booster: One Piece Card The Best vol.2 PRB-02 EN", set_code="PRB02",
        )
        seed_product(
            session, category_id=category_id,
            name_canonical="Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", set_code="PRB02",
        )
        seed_store_product(session, raw_name="SOBRE THE BEST VOL.2 – PRB-02 – ONE PIECE")

        result = matches_service.list_matches(session, MatchFilters())

        assert result.data[0].candidates[0].product_id == sobre_id


class TestConfirmMatch:
    def test_404_si_store_product_no_existe(self, session):
        with pytest.raises(NotFoundError):
            matches_service.confirm_match(session, 999, ConfirmBody(product_id=1))

    def test_422_si_product_id_no_existe(self, session):
        sp_id = seed_store_product(session)
        with pytest.raises(UnprocessableEntityError):
            matches_service.confirm_match(session, sp_id, ConfirmBody(product_id=999))

    def test_confirmar_actualiza_match_status_y_product_id(self, session):
        product_id, _ = seed_product(session)
        sp_id = seed_store_product(session)

        item = matches_service.confirm_match(session, sp_id, ConfirmBody(product_id=product_id))

        assert item.match_status.value == "confirmed"
        assert item.product_id == product_id

    def test_match_confidence_se_guarda_si_coincide_con_un_candidato(self, session):
        product_id, _ = seed_product(session, name_canonical="Booster Box: The Time of Battle OP-16 EN")
        sp_id = seed_store_product(session, raw_name="One Piece TCG OP16 Booster Box (EN)")

        item = matches_service.confirm_match(session, sp_id, ConfirmBody(product_id=product_id))

        assert item.match_confidence is not None

    def test_match_confidence_es_null_si_es_eleccion_manual_sin_relacion(self, session):
        product_id, _ = seed_product(session, name_canonical="Producto totalmente distinto")
        # store_product en OTRA categoría -- el candidato calculado en
        # caliente nunca podrá incluir a `product_id`.
        seed_category(session, slug="starter-deck")
        sp_id = seed_store_product(session, raw_name="Mazo de inicio Buggy")

        item = matches_service.confirm_match(session, sp_id, ConfirmBody(product_id=product_id))

        assert item.match_confidence is None


class TestRejectMatch:
    def test_marca_reviewed_at_y_limpia_product_id(self, session):
        product_id, _ = seed_product(session)
        sp_id = seed_store_product(session, match_status="confirmed", product_id=product_id)

        item = matches_service.reject_match(session, sp_id, RejectBody(mark_as="unmatched", reason="prueba"))

        assert item.match_status.value == "unmatched"
        assert item.product_id is None
        assert item.reviewed_at is not None

    def test_mark_as_needs_review_tambien_valido(self, session):
        sp_id = seed_store_product(session, match_status="unmatched")

        item = matches_service.reject_match(session, sp_id, RejectBody(mark_as="needsReview"))

        assert item.match_status.value == "needs_review"

    def test_404_si_no_existe(self, session):
        with pytest.raises(NotFoundError):
            matches_service.reject_match(session, 999, RejectBody(mark_as="unmatched"))


class TestReopenMatch:
    def test_deshace_confirmacion(self, session):
        product_id, _ = seed_product(session)
        sp_id = seed_store_product(session, match_status="confirmed", product_id=product_id)

        item = matches_service.reopen_match(session, sp_id)

        assert item.match_status.value == "needs_review"
        assert item.product_id is None
        assert item.reviewed_at is None

    def test_409_si_no_estaba_confirmado(self, session):
        sp_id = seed_store_product(session, match_status="needs_review")

        with pytest.raises(ConflictError):
            matches_service.reopen_match(session, sp_id)

    def test_limpia_un_rechazo_previo(self, session):
        product_id, _ = seed_product(session)
        sp_id = seed_store_product(session, match_status="confirmed", product_id=product_id,
                                    reviewed_at=datetime.now(timezone.utc))

        item = matches_service.reopen_match(session, sp_id)

        assert item.reviewed_at is None


class TestMissingCandidates:
    def test_agrupa_por_tipo_set_idioma_y_cuenta_tiendas_distintas(self, session):
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaA")
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaB")

        suggestions = matches_service.missing_candidates(session, min_stores=2)

        assert len(suggestions) == 1
        assert suggestions[0].store_count == 2

    def test_por_debajo_del_minimo_de_tiendas_no_aparece(self, session):
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaUnica")

        suggestions = matches_service.missing_candidates(session, min_stores=2)

        assert suggestions == []

    def test_si_ya_existe_candidato_en_esa_categoria_main_set_no_aparece(self, session):
        seed_product(session, main_set="OP17", name_canonical="Booster Box OP17 EN existente")
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaA")
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaB")

        suggestions = matches_service.missing_candidates(session, min_stores=2)

        assert suggestions == []

    def test_lote_cartas_y_otros_se_ignoran(self, session):
        seed_store_product(session, raw_name="Lote de 50 cartas sueltas", store_name="TiendaA")
        seed_store_product(session, raw_name="Lote de 50 cartas sueltas", store_name="TiendaB")

        suggestions = matches_service.missing_candidates(session, min_stores=2)

        assert suggestions == []


class TestRouterMatches:
    def test_get_matches_requiere_scope_admin(self, client, auth_headers):
        resp = client.get("/matches", headers=auth_headers)  # auth_headers solo trae "read"
        assert resp.status_code == 403

    def test_get_matches_con_scope_admin(self, session, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "API_KEYS", {"panel": frozenset({"read", "admin:*"})})
        seed_product(session)
        seed_store_product(session)

        resp = client.get("/matches", headers={"Authorization": "Bearer panel"})

        assert resp.status_code == 200
        assert resp.json()["data"][0]["storeProductId"] is not None

    def test_missing_candidates_via_router(self, session, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "API_KEYS", {"panel": frozenset({"admin:*"})})
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaA")
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaB")

        resp = client.get("/matches/missing-candidates", headers={"Authorization": "Bearer panel"})

        assert resp.status_code == 200
        assert resp.json()["data"][0]["storeCount"] == 2

    def test_status_invalido_es_422(self, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "API_KEYS", {"panel": frozenset({"admin:*"})})

        resp = client.get("/matches?status=no-existe", headers={"Authorization": "Bearer panel"})

        assert resp.status_code == 422

    def test_confirm_reject_reopen_ciclo_completo_via_router(self, session, client, monkeypatch):
        import config
        monkeypatch.setattr(config, "API_KEYS", {"panel": frozenset({"admin:*"})})
        H = {"Authorization": "Bearer panel"}
        product_id, _ = seed_product(session)
        sp_id = seed_store_product(session)

        confirm_resp = client.post(f"/matches/{sp_id}/confirm", json={"productId": product_id}, headers=H)
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["matchStatus"] == "confirmed"

        reopen_resp = client.post(f"/matches/{sp_id}/reopen", headers=H)
        assert reopen_resp.status_code == 200
        assert reopen_resp.json()["matchStatus"] == "needs_review"

        reject_resp = client.post(f"/matches/{sp_id}/reject", json={"markAs": "unmatched"}, headers=H)
        assert reject_resp.status_code == 200
        assert reject_resp.json()["matchStatus"] == "unmatched"

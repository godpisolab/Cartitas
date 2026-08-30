"""Tests del panel de matching -- docs/api-endpoints-v1.md sección 6 y
docs/api-endpoints-gestor.md sección 1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import services.matches as matches_service
from errors import ConflictError, NotFoundError, UnprocessableEntityError
from schemas.matches import ConfirmBody, MatchFilters, RejectBody


def seed_category(session, slug="one-piece"):
    row = session.exec(
        text("INSERT INTO category (name, slug) VALUES (:slug, :slug) RETURNING id"), params={"slug": slug},
    ).first()
    session.commit()
    return row[0]


def seed_store_product(session, *, raw_name="One Piece TCG OP16 Booster Box (EN)", store_name="Cardzone",
                        match_status="needs_review", product_id=None, reviewed_at=None,
                        reviewed_reason=None, raw_tags=None) -> int:
    store_id = session.exec(
        text("INSERT INTO store (name, website_url, platform) VALUES (:n, :u, 'shopify') RETURNING id"),
        params={"n": store_name, "u": f"https://{store_name.lower()}.example"},
    ).first()[0]
    sp_id = session.exec(
        text("""
            INSERT INTO store_product (store_id, product_id, match_status, store_url, raw_name,
                                        current_price, stock_status, reviewed_at, reviewed_reason, raw_tags)
            VALUES (:store_id, :product_id, :status, :url, :raw_name, 119.90, 'disponible',
                    :reviewed_at, :reviewed_reason, :raw_tags)
            RETURNING id
        """),
        params={"store_id": store_id, "product_id": product_id, "status": match_status,
                "url": f"https://x.example/{store_id}", "raw_name": raw_name, "reviewed_at": reviewed_at,
                "reviewed_reason": reviewed_reason, "raw_tags": raw_tags},
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
    return product_id, category_id


class TestListMatchesStatusFilter:
    def test_default_status_es_needs_review(self, session):
        _, category_id = seed_product(session, set_code="OP16", packaging="display")
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
        seed_product(session, set_code="OP16", packaging="display")
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


class TestOcultaHuecosDeCatalogo:
    """2026-08-30: needsReview/unmatched esconden por defecto filas cuya
    clasificación coincide con un hueco de catálogo YA CONOCIDO (mismo
    criterio que missing_candidates() -- mismo product_type/set_code/
    idioma/packaging sin ningún canónico, con demanda de 2+ tiendas
    distintas). No hay nada que un revisor pueda hacer con ellas hasta que
    se siembre el canónico -- se autorresuelven solas en cuanto exista.
    Nunca afecta a status=all (vista de auditoría completa a propósito)."""

    def test_hueco_de_catalogo_se_esconde_de_needs_review_por_defecto(self, session):
        seed_store_product(session, raw_name="One Piece TCG OP99 Booster Box (EN)", store_name="TiendaA")
        seed_store_product(session, raw_name="One Piece TCG OP99 Booster Box (EN)", store_name="TiendaB")

        result = matches_service.list_matches(session, MatchFilters())

        assert result.data == []

    def test_hueco_de_catalogo_reaparece_con_include_catalog_gaps(self, session):
        seed_store_product(session, raw_name="One Piece TCG OP99 Booster Box (EN)", store_name="TiendaA")
        seed_store_product(session, raw_name="One Piece TCG OP99 Booster Box (EN)", store_name="TiendaB")

        result = matches_service.list_matches(session, MatchFilters(include_catalog_gaps=True))

        assert len(result.data) == 2

    def test_hueco_de_catalogo_nunca_se_esconde_en_status_all(self, session):
        seed_store_product(session, raw_name="One Piece TCG OP99 Booster Box (EN)", store_name="TiendaA")
        seed_store_product(session, raw_name="One Piece TCG OP99 Booster Box (EN)", store_name="TiendaB")

        result = matches_service.list_matches(session, MatchFilters(status="all"))

        assert len(result.data) == 2

    def test_una_sola_tienda_no_cuenta_como_hueco_confirmado(self, session):
        # Por debajo de MISSING_CANDIDATE_MIN_STORES -- ruido de una sola
        # tienda, se sigue mostrando normal (mismo criterio que
        # missing_candidates()).
        seed_store_product(session, raw_name="One Piece TCG OP99 Booster Box (EN)", store_name="TiendaUnica")

        result = matches_service.list_matches(session, MatchFilters())

        assert len(result.data) == 1

    def test_si_ya_existe_canonico_no_se_esconde(self, session):
        seed_product(session, set_code="OP99", packaging="display")
        seed_store_product(session, raw_name="One Piece TCG OP99 Booster Box (EN)", store_name="TiendaA")
        seed_store_product(session, raw_name="One Piece TCG OP99 Booster Box (EN)", store_name="TiendaB")

        result = matches_service.list_matches(session, MatchFilters())

        assert len(result.data) == 2


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
    """Caso real encontrado revisando la cola: "Premium Booster" (sobre) y
    "Premium Booster Box" (caja) del mismo PRB-02 conviven en la MISMA
    categoría (premium-booster-box) con el MISMO set_code -- el desempate
    es ahora por la columna `packaging`, no por is_box_variant()."""

    def test_caja_prioriza_el_candidato_caja_mismo_set_code(self, session):
        category_id = seed_category(session, slug="premium-booster-box")
        sobre_id, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Premium Booster: One Piece Card The Best vol.2 PRB-02 EN", set_code="PRB02",
            packaging="sobre",
        )
        caja_id, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", set_code="PRB02",
            packaging="display",
        )
        seed_store_product(session, raw_name="CAJA THE BEST VOL.2 – PRB-02 – ONE PIECE")

        result = matches_service.list_matches(session, MatchFilters())

        assert result.data[0].candidates[0].product_id == caja_id
        assert result.data[0].candidates[0].product_id != sobre_id

    def test_sobre_prioriza_el_candidato_sobre_mismo_set_code(self, session):
        category_id = seed_category(session, slug="premium-booster-box")
        sobre_id, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Premium Booster: One Piece Card The Best vol.2 PRB-02 EN", set_code="PRB02",
            packaging="sobre",
        )
        seed_product(
            session, category_id=category_id,
            name_canonical="Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", set_code="PRB02",
            packaging="display",
        )
        seed_store_product(session, raw_name="SOBRE THE BEST VOL.2 – PRB-02 – ONE PIECE")

        result = matches_service.list_matches(session, MatchFilters())

        assert result.data[0].candidates[0].product_id == sobre_id


class TestCandidatosPriorizanIdioma:
    """docs/pendientes-motor-matching.md punto 6, mismo hallazgo que
    matcher._best_candidate (store_monitor/): el candidato EN y el JP del
    mismo set_code comparten casi todo el texto salvo el sufijo de idioma
    -- similarity() los deja prácticamente empatados, y sin este desempate
    el candidato #1 que ve quien revisa puede ser el idioma que NO
    coincide con lo que la tienda declaró explícitamente."""

    def test_japones_prioriza_el_candidato_jp_mismo_set_code(self, session):
        category_id = seed_category(session, slug="one-piece")
        en_id, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Booster Box: Adventure on Kami's Island OP-15 EN", set_code="OP15", language="EN",
        )
        jp_id, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Booster Box: Adventure on Kami's Island OP-15 JP", set_code="OP15", language="JP",
        )
        # Caso real (2026-08-28): así lo lista una tienda real -- sin el
        # desempate por idioma, el candidato #1 podía salir en EN pese a
        # que el propio raw_name dice "Japones" explícitamente.
        seed_store_product(session, raw_name="Caja One Piece Adventure on Kami's Island OP15 - Japones")

        result = matches_service.list_matches(session, MatchFilters())

        assert result.data[0].candidates[0].product_id == jp_id
        assert result.data[0].candidates[0].product_id != en_id

    def test_idioma_no_soportado_por_el_enum_no_revienta_la_consulta(self, session):
        # Bug real de producción (encontrado vía el panel, no un test):
        # "ONE PIECE EB03 HEROINES EDITION CAJA SOBRES COREANO" clasifica
        # language='KR' (shared.classify._detect_language) -- el ENUM
        # product_language de Postgres solo admite EN/JP/ES, así que pasar
        # "KR" tal cual a `Product.language == language` revienta la query
        # con psycopg2.errors.InvalidTextRepresentation en vez de
        # simplemente no encontrar un candidato del mismo idioma. Con
        # status='all' (el filtro que sí calcula candidatos para filas no
        # confirmadas, a diferencia de 'confirmed') se ejercita la misma
        # ruta que rompía en real.
        category_id = seed_category(session, slug="extra-booster")
        seed_product(
            session, category_id=category_id,
            name_canonical="Extra Booster: Heroines Edition EB-03 EN", set_code="EB03", language="EN",
        )
        seed_store_product(session, raw_name="ONE PIECE EB03 HEROINES EDITION CAJA SOBRES COREANO")

        result = matches_service.list_matches(session, MatchFilters(status="all"))

        assert len(result.data) == 1


class TestCandidatosUsanRawTags:
    """2026-08-27: el panel también usa raw_tags (Shopify), no solo el
    matcher automático -- verificado en vivo contra Pokemillon real que su
    product_type nativo viene vacío, pero tags sí trae señal fiable."""

    def test_raw_tags_encuentra_candidato_que_raw_name_solo_no_encuentra(self, session):
        # A diferencia del sistema anterior, un código en `name` (p.ej.
        # "OP13") ya resuelve la família sin necesitar tags en el pipeline
        # nuevo -- el caso que SIGUE necesitando tags es, por construcción,
        # uno sin código ni título reconocible: solo la keyword genérica de
        # sellado vive en las tags.
        category_id = seed_category(session, slug="one-piece")
        correcto_id, _ = seed_product(
            session, category_id=category_id,
            name_canonical="Booster Box: Carrying On His Will OP-13 EN", set_code="OP13",
        )
        seed_store_product(
            session, raw_name="His Will Carrying One Piece TCG Special Edition En",
            raw_tags="Caja One Piece, Cajas, Cajas de Sobres",
        )

        result = matches_service.list_matches(session, MatchFilters())

        assert len(result.data) == 1
        assert result.data[0].candidates[0].product_id == correcto_id


class TestCandidatosFallbackCrossCategoriaPorSetCode:
    """El mecanismo de fallback cross-categoría de _top_candidates() sigue
    existiendo para cuando un canónico se sembró (por error humano, o
    porque una tienda usa una convención distinta) en una categoría
    distinta a la que classify_with_category() deriva para un raw_name con
    ese mismo set_code -- código de família exclusivo (PRB, EB, DP...) es
    señal fuerte, no arrastra falsos positivos de otra família. En el
    pipeline nuevo, un código PRB-NN explícito YA resuelve directamente a
    premium-booster-box (Corrección 4: família exclusiva por código, sin
    depender de que además aparezca "the best"/"vol") -- el escenario real
    que motivó este fallback en el sistema anterior ya no ocurre para PRB,
    pero el mecanismo en sí sigue siendo necesario para el caso genérico
    (canónico sembrado en la categoría equivocada)."""

    def test_encuentra_el_candidato_de_otra_categoria_por_set_code_exacto(self, session):
        seed_category(session, slug="premium-booster-box")
        # Canónico sembrado por error en premium-card-collection (família
        # distinta) -- premium-booster-box, la categoría real que deriva
        # classify_with_category() para este raw_name, se queda vacía.
        premium_card_collection_id = seed_category(session, slug="premium-card-collection")
        prb_id, _ = seed_product(
            session, category_id=premium_card_collection_id,
            name_canonical="Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", set_code="PRB02",
            packaging="display",
        )
        seed_store_product(session, raw_name="One Piece Card Game Premium Booster PRB-02 Caja")

        result = matches_service.list_matches(session, MatchFilters())

        assert len(result.data) == 1
        assert result.data[0].candidates[0].product_id == prb_id


class TestFallbackCrossCategoriaNuncaParaCodigoVol:
    """Bug real corregido 2026-08-30: a diferencia de PRB02/OP17 (asignados
    por Bandai, SET_CODE_PREFIXES), "VOL{NN}" es un pseudo-código inventado
    por este proyecto y reutilizado de forma independiente por Illustration
    Box/Sleeves/Playmat/Premium Card Collection -- un "VOL09" de Sleeves no
    tiene nada que ver con un "VOL09" de Illustration Box. Encontrado en
    vivo: una fila real de "Illustration Box IB-09" sugería "Official
    Sleeves 9" como candidato."""

    def test_no_sugiere_un_candidato_vol_de_otra_categoria(self, session):
        illustration_box_id = seed_category(session, slug="illustration-box")
        sleeves_id = seed_category(session, slug="sleeves")
        sleeves_product_id, _ = seed_product(
            session, category_id=sleeves_id, name_canonical="Official Sleeves 9 EN", set_code="VOL09",
        )
        seed_product(
            session, category_id=illustration_box_id, name_canonical="Illustration Box Vol.1 EN", set_code="VOL01",
        )
        seed_store_product(
            session, raw_name="One Piece Card Game Illustration Box IB-09 Inglés | Pre-Reserva",
        )

        result = matches_service.list_matches(session, MatchFilters())

        assert len(result.data) == 1
        candidate_ids = [c.product_id for c in result.data[0].candidates]
        assert sleeves_product_id not in candidate_ids

    def test_missing_candidates_no_lo_esconde_por_un_vol_de_otra_categoria(self, session):
        seed_category(session, slug="illustration-box")
        sleeves_id = seed_category(session, slug="sleeves")
        seed_product(session, category_id=sleeves_id, name_canonical="Official Sleeves 9 EN", set_code="VOL09")
        seed_store_product(
            session, raw_name="One Piece Card Game Illustration Box IB-09 Inglés | Pre-Reserva",
            store_name="TiendaA",
        )
        seed_store_product(
            session, raw_name="One Piece Card Game Illustration Box IB-09 Inglés | Pre-Reserva",
            store_name="TiendaB",
        )

        suggestions = matches_service.missing_candidates(session, min_stores=2)

        assert len(suggestions) == 1
        assert suggestions[0].set_code == "VOL09"
        assert suggestions[0].product_type == "ILLUSTRATION_BOX"


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
        assert item.reviewed_reason == "prueba"

    def test_mark_as_needs_review_tambien_valido(self, session):
        sp_id = seed_store_product(session, match_status="unmatched")

        item = matches_service.reject_match(session, sp_id, RejectBody(mark_as="needsReview"))

        assert item.match_status.value == "needs_review"

    def test_reject_guarda_reason_cuando_se_proporciona(self, session):
        sp_id = seed_store_product(session, match_status="needs_review")

        item = matches_service.reject_match(
            session, sp_id, RejectBody(mark_as="unmatched", reason="Es un accesorio, no una caja"),
        )

        assert item.reviewed_reason == "Es un accesorio, no una caja"

    def test_reject_sin_reason_queda_null(self, session):
        sp_id = seed_store_product(session, match_status="needs_review")

        item = matches_service.reject_match(session, sp_id, RejectBody(mark_as="unmatched"))

        assert item.reviewed_reason is None

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

    def test_limpia_el_reason_de_un_rechazo_previo(self, session):
        product_id, _ = seed_product(session)
        sp_id = seed_store_product(session, match_status="confirmed", product_id=product_id,
                                    reviewed_at=datetime.now(timezone.utc), reviewed_reason="motivo previo")

        item = matches_service.reopen_match(session, sp_id)

        assert item.reviewed_reason is None


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

    def test_si_ya_existe_candidato_en_esa_categoria_set_code_no_aparece(self, session):
        seed_product(
            session, set_code="OP17", main_set="OP17", name_canonical="Booster Box OP17 EN existente",
            packaging="display",
        )
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaA")
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaB")

        suggestions = matches_service.missing_candidates(session, min_stores=2)

        assert suggestions == []

    def test_candidato_con_main_set_pero_set_code_null_no_evita_la_sugerencia(self, session):
        # Regresión (2026-08-27): Double Pack/Illustration Box tienen
        # set_code propio (DP-NN/VOL-NN) y main_set=NULL en la BBDD real --
        # un candidato con main_set poblado pero set_code NULL para un
        # grupo con set_code distinto NO debe contar como "ya existe".
        seed_product(session, set_code=None, main_set="OP17", name_canonical="Otro producto sin set_code")
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaA")
        seed_store_product(session, raw_name="Booster Box OP17 EN", store_name="TiendaB")

        suggestions = matches_service.missing_candidates(session, min_stores=2)

        assert len(suggestions) == 1
        assert suggestions[0].set_code == "OP17"

    def test_lote_cartas_y_otros_se_ignoran(self, session):
        seed_store_product(session, raw_name="Lote de 50 cartas sueltas", store_name="TiendaA")
        seed_store_product(session, raw_name="Lote de 50 cartas sueltas", store_name="TiendaB")

        suggestions = matches_service.missing_candidates(session, min_stores=2)

        assert suggestions == []

    def test_candidato_en_otra_categoria_con_el_mismo_set_code_no_genera_sugerencia(self, session):
        # Canónico sembrado (por error) en premium-card-collection en vez
        # de premium-booster-box -- el fallback cross-categoría por
        # set_code exacto evita que esto se reporte como "hueco real" del
        # catálogo.
        premium_card_collection_id = seed_category(session, slug="premium-card-collection")
        seed_category(session, slug="premium-booster-box")
        seed_product(
            session, category_id=premium_card_collection_id,
            name_canonical="Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", set_code="PRB02",
            packaging="display",
        )
        seed_store_product(session, raw_name="One Piece Card Game Premium Booster PRB-02 Caja", store_name="TiendaA")
        seed_store_product(session, raw_name="One Piece Card Game Premium Booster PRB-02 Caja", store_name="TiendaB")

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

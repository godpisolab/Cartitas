"""Tests del motor de matching -- Evidence Builder / Decision Policy
(docs/propuestas/guia_nuevo_matcher.md §9.1).

- El top-3 (C.3) NO se guarda como columna -- se calcula en caliente en un
  futuro endpoint (ver docstring de matcher.py). No hay `suggested_product_id`
  que testear; en su lugar se prueba que _evaluate()/run_matching() nunca
  auto-confirman un candidato ambiguo (documentado en la sección de
  candidatos empatados más abajo).
- Los umbrales se testean con _evaluate() y un candidato CONTROLADO
  (monkeypatch de _best_candidate) en vez de depender de que un texto real
  produzca una similarity() exacta -- los límites (0.6, 0.35) son de la
  LÓGICA de decisión, no de pg_trgm en sí.
- Taxonomía nueva: BOOSTER_BOX/BOOSTER_PACK/BOOSTER_CASE se funden en
  ONE_PIECE (categoría 'one-piece', desempate por `packaging` en vez de
  `is_box_variant`); PREMIUM_COLLECTION se separa en
  PREMIUM_CARD_COLLECTION/PREMIUM_BOOSTER_BOX; LEARN_DECK se funde en
  STARTER_DECK; PROMO_CARD/MYSTERY_PACK/DICE_ACCESSORY suben a Fase 0
  (not_applicable siempre, ya no tienen categoría real)."""

from __future__ import annotations

import pytest

import matcher
from shared.classify import cantidad_es_ambigua, classify_with_category
from shared.domain import Classification


def make_classification(product_type="ONE_PIECE", set_code="OP11", language="EN", main_set="OP11", packaging="sobre"):
    return Classification(
        product_type=product_type, set_code=set_code, language=language, main_set=main_set, packaging=packaging,
    )


def stub_candidate(
    monkeypatch, product_id=1, name_canonical="Stub Producto Canonico", set_code="OP11", language="EN",
    packaging="sobre", score=0.75, es_fallback=False,
):
    """El candidato fijo configurado se devuelve sea cual sea la búsqueda --
    name_canonical distinto de cualquier raw_name usado en los tests de este
    fichero a propósito, para que exact_name_match nunca dispare por
    accidente en un test que no lo pide explícitamente."""
    def _fake_best_candidate(cur, category_id, raw_name, set_code=None, language=None, packaging=None):
        candidate = (
            (product_id, candidate_name, candidate_set_code, candidate_language, candidate_packaging, score)
            if score is not None else None
        )
        return candidate, es_fallback

    candidate_name = name_canonical
    candidate_set_code = set_code
    candidate_language = language
    candidate_packaging = packaging
    monkeypatch.setattr(matcher, "_best_candidate", _fake_best_candidate)


def seed_category(conn, slug="one-piece", name="One Piece"):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO category (name, slug) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING",
                    (name, slug))
        cur.execute("SELECT id FROM category WHERE slug = %s", (slug,))
        category_id = cur.fetchone()[0]
    conn.commit()
    return category_id


def seed_canonical(conn, category_id, name_canonical, set_code, language="EN", packaging=None):
    """Producto canónico real (no stub) -- para tests que necesitan
    similarity() de pg_trgm de verdad, contra category_id concretos."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                    "ON CONFLICT (slug) DO NOTHING")
        cur.execute("SELECT id FROM game WHERE slug='one-piece'")
        game_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO product (game_id, category_id, set_code, language, packaging, name_canonical) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (game_id, category_id, set_code, language, packaging, name_canonical),
        )
        product_id = cur.fetchone()[0]
    conn.commit()
    return product_id


def seed_store_product(conn, raw_name, store_label="Tienda", raw_variant=None, tags=None):
    """Inserta un store_product real (vía persistence, como llegaría del
    scraper) -- usado por los tests end-to-end de run_matching()."""
    import persistence
    from shared.domain import Platform, Product, StoreConfig
    cfg = StoreConfig(store_label, f"https://{store_label.lower()}.example", Platform.SHOPIFY,
                       shopify_collection="x")
    store_ids = persistence.sync_stores(conn, [cfg])
    conn.commit()
    product = Product(store=store_label, platform="shopify", id_product=None, name=raw_name,
                       variant=raw_variant, product_type="", main_set=None, set_code=None, language=None,
                       price=10.0, stock_status="DISPONIBLE",
                       url=f"https://{store_label.lower()}.example/p1", sku=None, image_url=None, tags=tags)
    persistence._save_one_store(conn, store_ids[cfg.domain], [product], __import__("datetime").date.today())
    conn.commit()


def evaluar(conn, raw_name, raw_variant=None):
    """classify_with_category() + _evaluate() contra las categorías/
    candidatos YA sembrados en `conn` -- el camino real que sigue
    run_matching() para un raw_name, sin pasar por persistence."""
    with conn.cursor() as cur:
        cur.execute("SELECT slug, id FROM category")
        category_ids = dict(cur.fetchall())
    classification, category_slug = classify_with_category(raw_name, raw_variant)
    with conn.cursor() as cur:
        return matcher._evaluate(cur, category_ids, classification, category_slug, raw_name)


# ===========================================================================
# 6.1 -- Tabla de umbrales de confianza
# ===========================================================================

class TestUmbralesDeConfianza:
    """Camino DE SIEMPRE (legado): candidato sin las condiciones del
    auto-confirmado por set_code (aquí, forzado con classification.set_code=None
    -- "no se detectó código en el raw_name", el caso real en el que este
    camino sigue siendo el único disponible). Los umbrales de score
    (0.6/0.35) solo gobiernan ESTE camino -- ver TestAutoConfirmadoPorSetCode
    para el camino rápido, que ya no depende del score en absoluto."""

    def test_coincide_todo_similarity_075_confirmed(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", score=0.75)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "confirmed"
        assert outcome.match_confidence == 0.75

    def test_coincide_todo_similarity_061_justo_sobre_el_corte_confirmed(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.61)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "confirmed"

    def test_coincide_todo_similarity_060_justo_en_el_corte_NO_confirma(self, db_conn, monkeypatch):
        # Límite exacto: "score > CONFIRMED_SIMILARITY_THRESHOLD" (estrictamente
        # mayor, no >=) -- 0.60 exacto NO confirma, cae a needs_review.
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.60)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "needs_review"

    def test_coincide_todo_similarity_045_needs_review(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.45)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "needs_review"

    def test_idioma_no_coincide_degrada_a_needs_review_aunque_similarity_sea_alta(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="JP", score=0.90)  # candidato en JP
        classification = make_classification(language="EN")  # pero el raw_name se detectó como EN
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "needs_review"

    def test_idioma_no_detectado_tambien_degrada_a_needs_review(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", score=0.90)
        classification = make_classification(language=None)  # no se detectó idioma en el raw_name
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "needs_review"

    def test_similarity_034_unmatched(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.34)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "unmatched"

    def test_similarity_035_justo_en_el_corte_no_es_unmatched_por_ese_motivo(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", score=0.35)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "needs_review"

    def test_set_code_no_coincide_es_unmatched_pese_a_similarity_alta(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP99", language="EN", score=0.90)
        classification = make_classification(set_code="OP11")  # el raw_name es de OP11
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "unmatched"
        assert outcome.product_id is None

    def test_set_code_solo_en_un_lado_no_rechaza_por_ese_motivo(self, db_conn, monkeypatch):
        # Illustration Box/Devil Fruits Collection nunca tienen set_code
        # poblado en el catálogo (sembrados desde "Vol.N") -- si la tienda
        # SÍ trae un código reconocible ("IB-06"), comparar a secas
        # rechazaba de más. Que falte el dato en UN lado no es evidencia de
        # que sea otro lanzamiento.
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code=None, language="EN", score=0.75)  # candidato sin set_code
        classification = make_classification(set_code="IB06")  # pero el raw_name sí trae uno
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "confirmed"  # sigue el flujo normal de umbrales, no se rechaza

    def test_sin_ningun_candidato_en_la_categoria_es_unmatched(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=None)  # _best_candidate no encuentra nada
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, make_classification(), "one-piece", "raw name")
        assert outcome.match_status == "unmatched"


class TestExactNameMatch:
    """NUEVO (§9.1 de la guía): coincidencia EXACTA de name_canonical contra
    raw_name (case-insensitive) -> 'confirmed' directo, sin mirar nada más
    -- la señal más fuerte posible."""

    def test_nombre_exacto_confirma_pese_a_set_code_distinto(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, name_canonical="Booster Box OP-16 The Time of Battle",
                        set_code="OP99", language="JP", score=0.01)
        classification = make_classification(set_code="OP11", language="EN")
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(
                cur, {"one-piece": category_id}, classification, "one-piece", "Booster Box OP-16 The Time of Battle",
            )
        assert outcome.match_status == "confirmed"

    def test_nombre_case_insensitive(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, name_canonical="Booster Box OP-16 The Time of Battle", score=0.01)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(
                cur, {"one-piece": category_id}, make_classification(), "one-piece",
                "BOOSTER BOX OP-16 THE TIME OF BATTLE",
            )
        assert outcome.match_status == "confirmed"

    def test_nombre_parecido_pero_no_exacto_no_dispara_este_camino(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        # packaging distinto a propósito -- si coincidiera con el default
        # de make_classification() ("sobre"), las CINCO condiciones del
        # camino rápido (set_code+idioma+packaging+cantidad) se cumplirían
        # igual y confirmaría por esa vía, no por exact_name_match (que es
        # justo lo que este test quiere aislar).
        stub_candidate(monkeypatch, name_canonical="Booster Box OP-16 The Time of Battle",
                        set_code="OP16", language="EN", packaging="display", score=0.01)
        classification = make_classification(set_code="OP16", language="EN", packaging="sobre")
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(
                cur, {"one-piece": category_id}, classification, "one-piece",
                "Booster Box OP-16 The Time of Battle EN Nuevo",
            )
        assert outcome.match_status == "needs_review"  # score bajo, no exacto


# ===========================================================================
# Camino RÁPIDO -- auto-confirmado por set_code exacto (+ idioma + packaging
# + cantidad no ambigua). No depende del score de similitud.
# ===========================================================================

class TestAutoConfirmadoPorSetCode:
    def test_condiciones_confirman_aunque_la_similitud_sea_bajisima(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", packaging="sobre", score=0.05, es_fallback=False)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, make_classification(), "one-piece", "raw name")
        assert outcome.match_status == "confirmed"
        assert outcome.match_confidence == 0.05

    def test_candidato_por_fallback_no_confirma_aunque_todo_lo_demas_coincida(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", packaging="sobre", score=0.20, es_fallback=True)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, make_classification(), "one-piece", "raw name")
        assert outcome.match_status == "needs_review"

    def test_cantidad_ambigua_bloquea_el_camino_rapido(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", packaging="display", score=0.20, es_fallback=False)
        classification = make_classification(set_code="OP11", packaging="display")
        with db_conn.cursor() as cur:
            # "x12" en un ONE_PIECE display (estándar 24) -- cantidad_es_ambigua()
            # devuelve True, bloquea las condiciones del camino rápido.
            outcome = matcher._evaluate(
                cur, {"one-piece": category_id}, classification, "one-piece", "OP11 Caja de sobres x12",
            )
        assert outcome.match_status == "needs_review"

    def test_packaging_distinto_bloquea_el_camino_rapido(self, db_conn, monkeypatch):
        # NUEVO (packaging_match, sustituye a la vieja separación por
        # categoría): mismo set_code/idioma, pero el candidato es 'display'
        # y la classification es 'sobre' -- no es el mismo SKU, no debe
        # auto-confirmar por el camino rápido (puede seguir cayendo needs_review
        # por similitud si el texto se parece).
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", packaging="display", score=0.20, es_fallback=False)
        classification = make_classification(set_code="OP11", packaging="sobre")
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "needs_review"

    def test_packaging_none_en_classification_no_bloquea_el_camino_rapido(self, db_conn, monkeypatch):
        # Família sin dimensión de packaging (Illustration Box, Playmat...)
        # -- packaging=None en ambos lados no debe impedir el auto-confirmado.
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="VOL06", language="EN", packaging=None, score=0.05, es_fallback=False)
        classification = make_classification(
            product_type="ILLUSTRATION_BOX", set_code="VOL06", main_set=None, packaging=None,
        )
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, "one-piece", "raw name")
        assert outcome.match_status == "confirmed"


# ===========================================================================
# Categoría con un único SKU posible -- mecanismo genérico, probado con un
# category_id sintético (single_sku_categories se pasa explícito en el
# test, no hace falta que corresponda a una família real de un solo SKU).
# ===========================================================================

class TestCategoriaUnSoloSKU:
    def test_categoria_unico_sku_confirma_pese_a_similitud_bajisima(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn, slug="sleeves", name="Sleeves")
        stub_candidate(monkeypatch, set_code=None, language="EN", packaging=None, score=0.05, es_fallback=False)
        classification = make_classification(product_type="SLEEVES", set_code=None, main_set=None, packaging=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(
                cur, {"sleeves": category_id}, classification, "sleeves", "raw name",
                single_sku_categories={category_id},
            )
        assert outcome.match_status == "confirmed"
        assert outcome.match_confidence == 0.05

    def test_categoria_no_marcada_como_unico_sku_no_se_ve_afectada(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn, slug="sleeves", name="Sleeves")
        stub_candidate(monkeypatch, set_code=None, language="EN", packaging=None, score=0.5, es_fallback=False)
        classification = make_classification(product_type="SLEEVES", set_code=None, main_set=None, packaging=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(
                cur, {"sleeves": category_id}, classification, "sleeves", "raw name",
                single_sku_categories=set(),
            )
        assert outcome.match_status == "needs_review"

    def test_categoria_unico_sku_no_confirma_si_es_fallback(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn, slug="sleeves", name="Sleeves")
        stub_candidate(monkeypatch, set_code=None, language="EN", packaging=None, score=0.5, es_fallback=True)
        classification = make_classification(product_type="SLEEVES", set_code=None, main_set=None, packaging=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(
                cur, {"sleeves": category_id}, classification, "sleeves", "raw name",
                single_sku_categories={category_id},
            )
        assert outcome.match_status == "needs_review"

    def test_categoria_unico_sku_no_confirma_si_idioma_contradice(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn, slug="sleeves", name="Sleeves")
        stub_candidate(monkeypatch, set_code=None, language="EN", packaging=None, score=0.5, es_fallback=False)
        classification = make_classification(product_type="SLEEVES", set_code=None, language="JP", main_set=None, packaging=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(
                cur, {"sleeves": category_id}, classification, "sleeves", "raw name",
                single_sku_categories={category_id},
            )
        assert outcome.match_status == "needs_review"


# ===========================================================================
# _best_candidate -- set_code exacto gana a similitud pura (contra Postgres
# real: hace falta similarity() de pg_trgm de verdad, no un stub)
# ===========================================================================

class TestBestCandidatePrioridadDeSetCode:
    def _seed_product(self, conn, category_id, name_canonical, set_code, packaging=None):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, packaging, name_canonical) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (game_id, category_id, set_code, packaging, name_canonical),
            )
            return cur.fetchone()[0]

    def test_set_code_exacto_gana_aunque_tenga_menos_similitud_de_texto(self, db_conn):
        category_id = seed_category(db_conn, slug="starter-deck")
        generico_id = self._seed_product(
            db_conn, category_id, "Starter Deck ONE PIECE FILM edition ST-05 EN", "ST05",
        )
        correcto_id = self._seed_product(
            db_conn, category_id, "Starter Deck: Red Monkey.D.Luffy ST-31 EN", "ST31",
        )

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(
                cur, category_id, "LUFFY – STARTER DECK ONE PIECE – ST 31", set_code="ST31",
            )

        assert candidate[0] == correcto_id
        assert candidate[0] != generico_id
        assert es_fallback is False

    def test_sin_set_code_en_el_raw_name_se_queda_con_similitud_pura(self, db_conn):
        category_id = seed_category(db_conn, slug="starter-deck")
        self._seed_product(db_conn, category_id, "Starter Deck ONE PIECE FILM edition ST-05 EN", "ST05")
        mas_parecido_id = self._seed_product(
            db_conn, category_id, "Starter Deck ONE PIECE FILM edition ST-06 EN", "ST06",
        )

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(
                cur, category_id, "Starter Deck ONE PIECE FILM edition ST-06 EN", set_code=None,
            )

        assert candidate[0] == mas_parecido_id
        assert es_fallback is False

    def test_packaging_desempata_con_mismo_set_code(self, db_conn):
        # "Premium Booster" (sobre) y "Premium Booster Box" (caja) del mismo
        # PRB-02 conviven en la MISMA categoría con el MISMO set_code --
        # desempate por la columna packaging (NUEVO, sustituye a
        # is_box_variant()/ILIKE de texto).
        category_id = seed_category(db_conn, slug="premium-booster-box")
        sobre_id = self._seed_product(
            db_conn, category_id, "Premium Booster: One Piece Card The Best vol.2 PRB-02 EN", "PRB02",
            packaging="sobre",
        )
        caja_id = self._seed_product(
            db_conn, category_id, "Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", "PRB02",
            packaging="display",
        )

        with db_conn.cursor() as cur:
            candidato_para_caja, fallback_caja = matcher._best_candidate(
                cur, category_id, "CAJA THE BEST VOL.2 – PRB-02 – ONE PIECE", set_code="PRB02", packaging="display",
            )
            candidato_para_sobre, fallback_sobre = matcher._best_candidate(
                cur, category_id, "SOBRE THE BEST VOL.2 – PRB-02 – ONE PIECE", set_code="PRB02", packaging="sobre",
            )

        assert candidato_para_caja[0] == caja_id
        assert candidato_para_sobre[0] == sobre_id
        assert fallback_caja is False and fallback_sobre is False

    def test_sin_packaging_pasado_no_desempata_por_ese_motivo(self, db_conn):
        # Degradación segura: sin packaging (None), el criterio nuevo no
        # discrimina nada -- devuelve alguno de los dos sin reventar.
        category_id = seed_category(db_conn, slug="premium-booster-box")
        self._seed_product(
            db_conn, category_id, "Premium Booster: One Piece Card The Best vol.2 PRB-02 EN", "PRB02",
            packaging="sobre",
        )
        self._seed_product(
            db_conn, category_id, "Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", "PRB02",
            packaging="display",
        )

        with db_conn.cursor() as cur:
            candidate, _ = matcher._best_candidate(
                cur, category_id, "THE BEST VOL.2 – PRB-02 – ONE PIECE", set_code="PRB02",
            )

        assert candidate is not None

    def test_fallback_cross_categoria_por_set_code_exacto(self, db_conn):
        # El canónico PRB02 "The Best vol.2" se sembró en premium-booster-box,
        # pero varias tiendas lo listan sin la palabra "Premium" -- se
        # clasifica entonces como ONE_PIECE, categoría vacía para ese
        # set_code. Sin el fallback, _best_candidate() nunca lo encontraría
        # buscando solo dentro de one-piece.
        one_piece_category_id = seed_category(db_conn, slug="one-piece")
        premium_category_id = seed_category(db_conn, slug="premium-booster-box")
        prb_id = self._seed_product(
            db_conn, premium_category_id, "Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", "PRB02",
        )

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(
                cur, one_piece_category_id, "One Piece Card Game Premium Booster PRB-02 Caja", set_code="PRB02",
            )

        assert candidate is not None
        assert candidate[0] == prb_id
        assert es_fallback is True

    def test_sin_fallback_si_la_categoria_derivada_ya_trae_el_set_code_exacto(self, db_conn):
        category_id = seed_category(db_conn, slug="one-piece")
        correcto_id = self._seed_product(db_conn, category_id, "Booster Box OP17 EN", "OP17")

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(cur, category_id, "Booster Box OP17 EN", set_code="OP17")

        assert candidate[0] == correcto_id
        assert es_fallback is False

    def test_fallback_no_dispara_sin_set_code(self, db_conn):
        category_id = seed_category(db_conn, slug="one-piece")

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(
                cur, category_id, "Producto sin código reconocible", set_code=None,
            )

        assert candidate is None
        assert es_fallback is False

    def test_idioma_desempata_cuando_en_y_jp_empatan_en_similitud(self, db_conn):
        category_id = seed_category(db_conn, slug="one-piece")
        en_id = seed_canonical(db_conn, category_id, "Booster Box: Carrying On His Will OP-13 EN", "OP13", "EN")
        jp_id = seed_canonical(db_conn, category_id, "Booster Box: Carrying On His Will OP-13 JP", "OP13", "JP")

        with db_conn.cursor() as cur:
            candidato_en, _ = matcher._best_candidate(
                cur, category_id, "One Piece OP13 Carrying On His Will", set_code="OP13", language="EN",
            )
            candidato_jp, _ = matcher._best_candidate(
                cur, category_id, "One Piece OP13 Carrying On His Will", set_code="OP13", language="JP",
            )

        assert candidato_en[0] == en_id
        assert candidato_jp[0] == jp_id

    def test_sin_idioma_pasado_no_desempata_por_ese_motivo(self, db_conn):
        category_id = seed_category(db_conn, slug="one-piece")
        seed_canonical(db_conn, category_id, "Booster Box: Carrying On His Will OP-13 EN", "OP13", "EN")
        seed_canonical(db_conn, category_id, "Booster Box: Carrying On His Will OP-13 JP", "OP13", "JP")

        with db_conn.cursor() as cur:
            candidate, _ = matcher._best_candidate(
                cur, category_id, "One Piece OP13 Carrying On His Will", set_code="OP13",
            )

        assert candidate is not None

    def test_idioma_no_soportado_por_el_enum_no_revienta_la_query(self, db_conn):
        # Bug real encontrado en producción: classify_product() puede
        # detectar "KR" (coreano, _detect_language en shared/classify.py)
        # a partir de un raw_name real ("...CAJA SOBRES COREANO"), pero el
        # ENUM product_language de Postgres solo admite EN/JP/ES -- pasar
        # "KR" tal cual a la comparación `(language = %s) IS TRUE` del
        # ORDER BY revienta con psycopg2.errors.InvalidTextRepresentation
        # ("invalid input value for enum product_language"), no con un
        # resultado vacío. Se degrada a "idioma no detectado" para la
        # consulta, igual que language=None.
        category_id = seed_category(db_conn, slug="extra-booster")
        candidate_id = seed_canonical(db_conn, category_id, "Extra Booster: Heroines Edition EB-03 EN", "EB03", "EN")

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(
                cur, category_id, "ONE PIECE EB03 HEROINES EDITION CAJA SOBRES COREANO",
                set_code="EB03", language="KR", packaging="display",
            )

        assert candidate is not None
        assert candidate[0] == candidate_id
        assert es_fallback is False

    def test_candidato_por_fallback_cross_categoria_se_marca_como_tal(self, db_conn):
        # La categoría sleeves se queda vacía a propósito, el único
        # candidato posible con set_code OP13 vive en one-piece.
        sleeves_category_id = seed_category(db_conn, slug="sleeves")
        one_piece_category_id = seed_category(db_conn, slug="one-piece")
        self._seed_product(db_conn, one_piece_category_id, "Booster Pack OP-13 Carrying On His Will EN", "OP13")

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(
                cur, sleeves_category_id, "Funda edición especial OP13", set_code="OP13",
            )

        assert candidate is not None
        assert es_fallback is True

    def test_candidato_dentro_de_su_categoria_no_se_marca_como_fallback(self, db_conn):
        category_id = seed_category(db_conn, slug="one-piece")
        correcto_id = self._seed_product(db_conn, category_id, "Caja OP16 The Time of Battle EN", "OP16")

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(cur, category_id, "Caja OP16 ...", set_code="OP16")

        assert candidate[0] == correcto_id
        assert es_fallback is False

    def test_fallback_cross_categoria_nunca_dispara_para_codigo_vol(self, db_conn):
        # Bug real corregido 2026-08-30: "VOL{NN}" es un pseudo-código
        # INVENTADO por este proyecto, reutilizado de forma independiente
        # por Illustration Box/Sleeves/Playmat/Premium Card Collection --
        # "VOL09" de una família no tiene NADA que ver con "VOL09" de otra,
        # a diferencia de "PRB02"/"OP17" (SET_CODE_PREFIXES, sí asignados
        # por Bandai). Encontrado en vivo: una fila real de "Illustration
        # Box IB-09" sugería "Official Sleeves 9" como candidato porque el
        # fallback cross-categoría no distinguía esto.
        illustration_box_id = seed_category(db_conn, slug="illustration-box")
        sleeves_id = seed_category(db_conn, slug="sleeves")
        self._seed_product(db_conn, sleeves_id, "Official Sleeves 9 EN", "VOL09")
        self._seed_product(db_conn, illustration_box_id, "Illustration Box Vol.1 EN", "VOL01")

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(
                cur, illustration_box_id, "One Piece Card Game Illustration Box IB-09 Inglés | Pre-Reserva",
                set_code="VOL09",
            )

        # Nunca el canónico de sleeves, aunque coincida el set_code -- se
        # queda con el mejor candidato DENTRO de illustration-box (el único
        # que hay, Vol.1, con similitud baja) en vez de cruzar de categoría.
        assert candidate is not None
        assert candidate[2] != "VOL09"
        assert es_fallback is False


# ===========================================================================
# 6.2 -- Exclusión de tipos no comparables
# ===========================================================================

class TestExclusionNotApplicable:
    @pytest.mark.parametrize("product_type", ["LOTE_CARTAS", "OTROS", "PROMO_CARD", "MYSTERY_PACK", "DICE_ACCESSORY"])
    def test_not_applicable_sin_consultar_similitud(self, db_conn, monkeypatch, product_type):
        # PROMO_CARD/MYSTERY_PACK/DICE_ACCESSORY suben a Fase 0 (NUEVO,
        # cierra §2 de la propuesta) -- ya nunca se comparan, igual que
        # LOTE_CARTAS/OTROS. Si _evaluate consultara _best_candidate para
        # cualquiera de estos, el stub haría que confirmara -- si el test
        # pasa con not_applicable, confirma que ni siquiera se llega a
        # consultarlo.
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.99)
        classification = make_classification(product_type=product_type)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"one-piece": category_id}, classification, None, "cualquier cosa")
        assert outcome.match_status == "not_applicable"


# ===========================================================================
# run_matching() end-to-end contra Postgres real
# ===========================================================================

class TestRunMatchingEndToEnd:
    def _seed_store_product(self, conn, raw_name, store_label="Tienda", tags=None):
        import persistence
        from shared.domain import Platform, Product, StoreConfig
        cfg = StoreConfig(store_label, f"https://{store_label.lower()}.example", Platform.SHOPIFY,
                           shopify_collection="x")
        store_ids = persistence.sync_stores(conn, [cfg])
        conn.commit()
        product = Product(store=store_label, platform="shopify", id_product=None, name=raw_name,
                           variant=None, product_type="", main_set=None, set_code=None, language=None,
                           price=10.0, stock_status="DISPONIBLE",
                           url=f"https://{store_label.lower()}.example/p1", sku=None, image_url=None, tags=tags)
        persistence._save_one_store(conn, store_ids[cfg.domain], [product], __import__("datetime").date.today())
        conn.commit()

    def test_sin_categorias_sembradas_lanza_runtimeerror_claro(self, db_conn):
        with pytest.raises(RuntimeError, match="seed-catalog"):
            matcher.run_matching(db_conn)

    def test_producto_lote_cartas_queda_not_applicable_tras_run_matching(self, db_conn):
        seed_category(db_conn)
        self._seed_store_product(db_conn, "Lote de 50 cartas sueltas One Piece")

        counts = matcher.run_matching(db_conn)

        assert counts == {"not_applicable": 1}
        with db_conn.cursor() as cur:
            cur.execute("SELECT match_status FROM store_product")
            assert cur.fetchone()[0] == "not_applicable"

    def test_run_matching_lee_raw_tags_de_bbdd_y_lo_usa_para_clasificar(self, db_conn):
        # Integración completa: raw_name a secas no trae ni código ni título
        # ni keyword de sellado (quedaría OTROS/not_applicable) -- solo con
        # las tags guardadas en BBDD (persistence -> raw_tags ->
        # run_matching las lee -> classify_with_category las usa) llega a
        # needs_review con un candidato real. A diferencia del sistema
        # anterior, aquí NO se puede poner el código en `name` a la vez --
        # en el pipeline nuevo un código en `name` ya resuelve la família
        # sin necesitar tags en absoluto (§4.5 de la propuesta), así que el
        # caso que SIGUE necesitando tags es, por construcción, uno sin
        # código -- el candidato se alcanza por similitud de texto, no por
        # set_code exacto.
        category_id = seed_category(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, name_canonical) "
                "VALUES (%s, %s, 'OP13', %s)",
                (game_id, category_id, "Booster Box: Carrying On His Will OP-13 EN"),
            )
        db_conn.commit()

        self._seed_store_product(
            db_conn, "His Will Carrying One Piece TCG Special Edition En",
            tags="Caja One Piece, Cajas, Cajas de Sobres",
        )

        matcher.run_matching(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT match_status FROM store_product")
            assert cur.fetchone()[0] == "needs_review"

    def test_confirmed_no_se_reevalua_en_la_siguiente_pasada(self, db_conn):
        category_id = seed_category(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, main_set, language, name_canonical) "
                "VALUES (%s, %s, 'OP11', 'EN', %s) RETURNING id",
                (game_id, category_id, "Booster Box OP11 EN"),
            )
            product_id = cur.fetchone()[0]
        db_conn.commit()

        self._seed_store_product(db_conn, "Booster Box OP11 EN")
        matcher.run_matching(db_conn)

        with db_conn.cursor() as cur:
            cur.execute("SELECT match_status FROM store_product")
            first_status = cur.fetchone()[0]

        if first_status == "confirmed":
            with db_conn.cursor() as cur:
                cur.execute("UPDATE store_product SET product_id = %s, match_confidence = 0.99", (product_id,))
            db_conn.commit()
            matcher.run_matching(db_conn)
            with db_conn.cursor() as cur:
                cur.execute("SELECT match_confidence FROM store_product")
                assert float(cur.fetchone()[0]) == 0.99  # no se reevaluó, se quedó el valor manual


# ===========================================================================
# find_missing_canonical_candidates()
# ===========================================================================

class TestFindMissingCanonicalCandidates:
    def _seed_store_product(self, conn, raw_name, store_label, tags=None):
        import persistence
        from shared.domain import Platform, Product, StoreConfig
        cfg = StoreConfig(store_label, f"https://{store_label.lower()}.example", Platform.SHOPIFY,
                           shopify_collection="x")
        store_ids = persistence.sync_stores(conn, [cfg])
        conn.commit()
        product = Product(store=store_label, platform="shopify", id_product=None, name=raw_name,
                           variant=None, product_type="", main_set=None, set_code=None, language=None,
                           price=10.0, stock_status="DISPONIBLE",
                           url=f"https://{store_label.lower()}.example/p1", sku=None, image_url=None, tags=tags)
        persistence._save_one_store(conn, store_ids[cfg.domain], [product], __import__("datetime").date.today())
        conn.commit()

    def test_varias_tiendas_sin_candidato_genera_sugerencia(self, db_conn):
        seed_category(db_conn, slug="one-piece")
        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaA")
        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaB")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert len(suggestions) == 1
        assert suggestions[0]["main_set"] == "OP17"
        assert suggestions[0]["store_count"] == 2

    def test_una_sola_tienda_no_genera_sugerencia(self, db_conn):
        seed_category(db_conn, slug="one-piece")
        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaA")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert suggestions == []

    def test_agrupa_usando_raw_tags_cuando_name_solo_no_basta(self, db_conn):
        # Sin código ni título en `name` (en el pipeline nuevo, si el código
        # estuviera en `name`, ya resolvería sin tags -- ver §4.5), solo la
        # keyword genérica de sellado vive en las tags -- suficiente para
        # que ya no caiga en OTROS (not_applicable) y SÍ se agrupe.
        seed_category(db_conn, slug="one-piece")
        self._seed_store_product(
            db_conn, "One Piece Something Without Any Signal", "TiendaA",
            tags="Caja One Piece, Cajas, Cajas de Sobres",
        )
        self._seed_store_product(
            db_conn, "One Piece Something Without Any Signal", "TiendaB",
            tags="Caja One Piece, Cajas, Cajas de Sobres",
        )

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert len(suggestions) == 1
        assert suggestions[0]["product_type"] == "ONE_PIECE"
        assert suggestions[0]["set_code"] is None

    def test_si_ya_existe_candidato_no_genera_sugerencia(self, db_conn):
        category_id = seed_category(db_conn, slug="one-piece")
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, main_set, packaging, name_canonical) "
                "VALUES (%s, %s, 'OP17', 'OP17', 'display', 'x')",
                (game_id, category_id),
            )
        db_conn.commit()

        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaA")
        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaB")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert suggestions == []

    def test_mismo_set_code_pero_packaging_distinto_si_genera_sugerencia(self, db_conn):
        # NUEVO: con packaging en la clave de agrupación, un canónico
        # 'sobre' ya sembrado no debe esconder que falta la variante
        # 'display' del mismo set_code -- son productos de precio
        # completamente distinto.
        category_id = seed_category(db_conn, slug="one-piece")
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, main_set, packaging, name_canonical) "
                "VALUES (%s, %s, 'OP17', 'OP17', 'sobre', 'x')",
                (game_id, category_id),
            )
        db_conn.commit()

        self._seed_store_product(db_conn, "One Piece Booster Box OP17 Display EN", "TiendaA")
        self._seed_store_product(db_conn, "One Piece Booster Box OP17 Display EN", "TiendaB")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert len(suggestions) == 1
        assert suggestions[0]["packaging"] == "display"

    def test_candidato_con_main_set_pero_set_code_null_no_evita_la_sugerencia(self, db_conn):
        category_id = seed_category(db_conn, slug="one-piece")
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, main_set, name_canonical) "
                "VALUES (%s, %s, NULL, 'OP17', 'x')",
                (game_id, category_id),
            )
        db_conn.commit()

        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaA")
        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaB")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert len(suggestions) == 1
        assert suggestions[0]["set_code"] == "OP17"

    def test_candidato_en_otra_categoria_con_el_mismo_set_code_no_genera_sugerencia(self, db_conn):
        # Caso real PRB02: el canónico vive en premium-booster-box, pero las
        # tiendas que no dicen "Premium" se clasifican como ONE_PIECE -- sin
        # el fallback cross-categoría, esto aparecería como "falta sembrar"
        # pese a que el producto ya existe con ese set_code exacto.
        one_piece_id = seed_category(db_conn, slug="one-piece")
        premium_id = seed_category(db_conn, slug="premium-booster-box")
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, packaging, name_canonical) "
                "VALUES (%s, %s, 'PRB02', 'display', 'Premium Booster Box: The Best vol.2 PRB-02 EN')",
                (game_id, premium_id),
            )
        db_conn.commit()
        assert one_piece_id  # categoría creada para que classify_with_category la resuelva

        self._seed_store_product(db_conn, "One Piece Card Game Premium Booster Box PRB-02 Caja", "TiendaA")
        self._seed_store_product(db_conn, "One Piece Card Game Premium Booster Box PRB-02 Caja", "TiendaB")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert suggestions == []

    def test_candidato_vol_en_otra_categoria_SI_genera_sugerencia(self, db_conn):
        # Bug real corregido 2026-08-30: al contrario que PRB02 (arriba),
        # "VOL{NN}" es un pseudo-código inventado por este proyecto,
        # reutilizado de forma independiente por Illustration Box/Sleeves/
        # Playmat/Premium Card Collection -- un "Official Sleeves 9"
        # (VOL09) en sleeves NUNCA cuenta como "ya existe canónico" para un
        # hueco real de Illustration Box VOL09. Antes de la corrección esto
        # escondía huecos reales de catálogo.
        illustration_box_id = seed_category(db_conn, slug="illustration-box")
        sleeves_id = seed_category(db_conn, slug="sleeves")
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, name_canonical) "
                "VALUES (%s, %s, 'VOL09', 'Official Sleeves 9 EN')",
                (game_id, sleeves_id),
            )
        db_conn.commit()
        assert illustration_box_id  # categoría creada para que classify_with_category la resuelva

        self._seed_store_product(
            db_conn, "One Piece Card Game Illustration Box IB-09 Inglés | Pre-Reserva", "TiendaA",
        )
        self._seed_store_product(
            db_conn, "One Piece Card Game Illustration Box IB-09 Inglés | Pre-Reserva", "TiendaB",
        )

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert len(suggestions) == 1
        assert suggestions[0]["set_code"] == "VOL09"
        assert suggestions[0]["product_type"] == "ILLUSTRATION_BOX"


# ===========================================================================
# cantidad_es_ambigua() -- unitarios directos, sin necesitar BBDD. Firma
# NUEVA: (raw_name, product_type, packaging) -- antes (raw_name,
# category_slug). Con packaging como campo dentro de una única categoría,
# la cantidad esperada depende de AMBOS (product_type, packaging), no solo
# de la categoría.
# ===========================================================================

class TestCantidadEsAmbigua:
    @pytest.mark.parametrize("raw_name,product_type,packaging,esperado", [
        ("Caja de 24 Sobres Royal Blood OP10 - Inglés", "ONE_PIECE", "display", False),
        ("Caja de 20 Sobres The Best 2 PRB02 - Inglés", "PREMIUM_BOOSTER_BOX", "display", False),
        ("[INGLÉS] One Piece Card Game OP-16 Caja de sobres x12", "ONE_PIECE", "display", True),
        ("Pack 5 Sobres One Piece Adventure on KAMI's Island OP15 - Japones", "ONE_PIECE", "sobre", True),
        ("[INGLÉS] One Piece Card Game Starter Deck EX Gear5 [ST21] x6", "STARTER_DECK", "sobre", True),
        (
            "One Piece Card Game Double Pack Set Vol.10 [DP-10] – 2 Booster Packs + Exclusive DON!! Card",
            "DOUBLE_PACK", "sobre", False,
        ),  # "2 Booster Packs" describe el contenido normal de un Double Pack, no un bundle de 2 sets
        ("Caja de 12 Sobres OP16 The Time of Battle", "ONE_PIECE", "case", False),  # case real = 12, coincide
        ("Caja de 24 Sobres OP16 The Time of Battle", "ONE_PIECE", "case", True),  # 24 != 12 esperado para case
    ])
    def test_cantidad_es_ambigua_casos_reales(self, raw_name, product_type, packaging, esperado):
        assert cantidad_es_ambigua(raw_name, product_type, packaging) == esperado

    def test_familia_sin_dimension_de_packaging_es_unidad_unica(self):
        # Illustration Box/Playmat/Sleeves/Devil Fruits/Premium Card
        # Collection no están en _PACKAGING_UNITS -- mismo criterio que el
        # sistema anterior (_CATEGORIAS_UNIDAD_UNICA ya incluía
        # illustration-box/playmat/devil-fruits-collection): cualquier
        # cantidad >1 es sospechosa de bundle real.
        assert cantidad_es_ambigua("Pack 5 Sobres de algo", "ILLUSTRATION_BOX", None) is True
        assert cantidad_es_ambigua("Pack 2 de algo", "PLAYMAT", None) is True

    def test_familia_sin_dimension_de_packaging_cantidad_1_no_es_ambigua(self):
        assert cantidad_es_ambigua("Illustration Box Vol.1 (1 unidad)", "ILLUSTRATION_BOX", None) is False

    def test_product_type_desconocido_no_revienta_y_sigue_criterio_de_unidad_unica(self):
        # No hay ya un "tercer caso" de category_slug reconocido pero fuera
        # de ambas tablas (con packaging dentro de una única categoría, los
        # 10 product_type reales o están en _PACKAGING_UNITS o se tratan
        # como unidad única) -- un valor inventado se comporta igual que
        # cualquier família de unidad única, sin reventar.
        assert cantidad_es_ambigua("Producto suelto sin cantidad mencionada", "TIPO_INVENTADO", None) is False


# ===========================================================================
# Control positivo: casos reales revisados a mano, todos confirmados como el
# producto correcto. Regresión: si alguno deja de confirmar, algo del
# cambio rompió el camino feliz.
# ===========================================================================

class TestControlPositivoCasosReales:
    @pytest.mark.parametrize("raw_name,raw_variant,category_slug,set_code,packaging,canonical_name", [
        (
            "One Piece: Double Pack Set Display DP-11", None, "double-pack", "DP11", "sobre",
            "One Piece Double Pack Set Vol.11 DP-11 EN",
        ),
        (
            "One Piece Card Game Playmat Limited Edition Vol 2", None, "playmat", "VOL02", None,
            "Playmat Vol.2 EN",
        ),
        (
            "One Piece | Illustration Box Vol.4 Perona & Mihawk", "Inglés", "illustration-box", "VOL04", None,
            "Illustration Box Vol.4 Perona & Mihawk EN",
        ),
        (
            "One Piece Card Game - Devil Fruits Collection Vol.3 Op-Op Fruit (DF03)", None,
            "devil-fruits-collection", "DF03", None, "Devil Fruits Collection Vol.3 Op-Op Fruit DF03 EN",
        ),
        (
            "Caja de 20 Sobres The Best 2 PRB02 - Inglés", None, "premium-booster-box", "PRB02", "display",
            "Caja de 20 Sobres The Best Vol.2 PRB-02 EN",
        ),
        (
            "One Piece Card Game - Gear 5 Starter Deck EX ST21", None, "starter-deck", "ST21", "sobre",
            "Starter Deck EX Gear 5 ST-21 EN",
        ),
        (
            "Caja sobres One Piece OP-16 The Time of Battle (inglés)", None, "one-piece", "OP16", "display",
            "Caja de Sobres One Piece OP-16 The Time of Battle EN",
        ),
        (
            "ONE PIECE TCG - EB-05", None, "extra-booster", "EB05", "sobre",
            "Booster Pack EB-05 One Piece TCG EN",
        ),
    ])
    def test_setcode_exacto_confirma_casos_reales_verificados_a_mano(
        self, db_conn, raw_name, raw_variant, category_slug, set_code, packaging, canonical_name,
    ):
        category_id = seed_category(db_conn, slug=category_slug, name=category_slug)
        seed_canonical(db_conn, category_id, canonical_name, set_code, language="EN", packaging=packaging)

        outcome = evaluar(db_conn, raw_name, raw_variant)

        assert outcome.match_status == "confirmed", (raw_name, outcome)


# ===========================================================================
# Falsos positivos a evitar: casos reales que comparten set_code (o casi)
# con un candidato real y que, sin las guardas de este diseño, confirmarían
# incorrectamente.
# ===========================================================================

class TestFalsosPositivosCasosReales:
    def test_promo_card_es_not_applicable_nunca_confirma(self, db_conn):
        # PROMO_CARD sube a Fase 0 (NUEVO) -- ya no depende del fallback
        # cross-categoría para no confirmar, simplemente nunca entra al
        # pipeline de matching en absoluto.
        one_piece_id = seed_category(db_conn, slug="one-piece")
        seed_canonical(db_conn, one_piece_id, "Booster Pack OP-13 Carrying On His Will EN", "OP13", "EN")

        outcome = evaluar(db_conn, "Carta Promo Sellada Ichiban Kuji Monkey D. Luffy OP13 - Japones")

        assert outcome.match_status == "not_applicable"

    def test_case_no_confirma_contra_premium_booster_box_sobre(self, db_conn):
        # packaging_match: es un case (10 cajas), el único candidato
        # sembrado es 'display' -- no debe confirmar por el camino rápido
        # (llega solo por fallback cross-categoría de todas formas, ya que
        # premium-booster-box aquí solo tiene la variante display).
        premium_id = seed_category(db_conn, slug="premium-booster-box")
        seed_canonical(db_conn, premium_id, "Caja de 20 Sobres The Best Vol.2 PRB-02 EN", "PRB02", "EN", packaging="display")

        outcome = evaluar(db_conn, "(CASE) THE BEST 2 – PRB-02 – x10 Booster Box- One Piece Card Game")

        assert outcome.match_status != "confirmed"

    def test_pack_5_sobres_no_confirma_contra_extra_booster_suelto(self, db_conn):
        extra_booster_id = seed_category(db_conn, slug="extra-booster")
        seed_canonical(db_conn, extra_booster_id, "Booster Pack OP-15 Adventure on KAMI's Island EN", "OP15", "EN", packaging="sobre")

        outcome = evaluar(db_conn, "Pack 5 Sobres One Piece Adventure on KAMI’s Island OP15 - Japones")

        assert outcome.match_status != "confirmed"

    def test_caja_x12_no_confirma_contra_one_piece_display_de_24(self, db_conn):
        one_piece_id = seed_category(db_conn, slug="one-piece")
        seed_canonical(db_conn, one_piece_id, "Caja de Sobres One Piece OP-16 The Time of Battle EN", "OP16", "EN", packaging="display")

        outcome = evaluar(db_conn, "[INGLÉS] One Piece Card Game OP-16 Caja de sobres x12")

        assert outcome.match_status != "confirmed"

    def test_starter_deck_x6_no_confirma_contra_mazo_suelto(self, db_conn):
        starter_deck_id = seed_category(db_conn, slug="starter-deck")
        seed_canonical(db_conn, starter_deck_id, "Starter Deck EX Gear 5 ST-21 EN", "ST21", "EN", packaging="sobre")

        outcome = evaluar(db_conn, "[INGLÉS] One Piece Card Game Starter Deck EX Gear5 [ST21] x6")

        assert outcome.match_status != "confirmed"

    def test_idioma_jp_no_confirma_contra_extra_booster_en(self, db_conn):
        extra_booster_id = seed_category(db_conn, slug="extra-booster")
        seed_canonical(db_conn, extra_booster_id, "Extra Booster OP-03 Pillars of Strength EN", "EB03", "EN", packaging="sobre")

        outcome = evaluar(db_conn, "One Piece | Sobres EB-03 Pillars of Strength", "Japonés")

        assert outcome.match_status != "confirmed"

    def test_idioma_jp_no_confirma_contra_premium_booster_box_en(self, db_conn):
        premium_id = seed_category(db_conn, slug="premium-booster-box")
        seed_canonical(db_conn, premium_id, "Caja de 20 Sobres The Best Vol.2 PRB-02 EN", "PRB02", "EN", packaging="display")

        outcome = evaluar(db_conn, "Caja One Piece The Best 2 PRB02 - Japones")

        assert outcome.match_status != "confirmed"

    def test_setcode_inexistente_no_confirma_contra_otro_lanzamiento(self, db_conn):
        one_piece_id = seed_category(db_conn, slug="one-piece")
        seed_canonical(db_conn, one_piece_id, "Booster Pack OP-13 Carrying On His Will EN", "OP13", "EN", packaging="sobre")

        outcome = evaluar(db_conn, "One Piece Card Game OP-18 Booster Pack - English")

        assert outcome.match_status != "confirmed"
        assert outcome.product_id is None

    def test_raw_name_coreano_no_revienta_run_matching(self, db_conn):
        # Bug real de producción (encontrado vía el panel, no un test):
        # "ONE PIECE EB03 HEROINES EDITION CAJA SOBRES COREANO" clasifica
        # language='KR' -- el ENUM product_language de Postgres no admite
        # ese valor, y pasarlo tal cual a _best_candidate()/_evaluate()
        # reventaba con psycopg2.errors.InvalidTextRepresentation en vez de
        # simplemente no confirmar (no existe canónico coreano, ni tendría
        # por qué). Integración completa de principio a fin: classify_with_category
        # -> _evaluate -> _best_candidate, la misma ruta que run_matching().
        extra_booster_id = seed_category(db_conn, slug="extra-booster")
        seed_canonical(db_conn, extra_booster_id, "Extra Booster: Heroines Edition EB-03 EN", "EB03", "EN", packaging="display")

        outcome = evaluar(db_conn, "ONE PIECE EB03 HEROINES EDITION CAJA SOBRES COREANO")

        assert outcome.match_status != "confirmed"  # idioma no coincide (KR vs EN) -- needs_review, no crash
        assert outcome.product_id is None

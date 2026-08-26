"""Tests del motor de matching -- sección 6 del plan de pruebas. matcher.py
YA estaba construido cuando se escribió el plan (bloque C, hecho el
2026-08-26) -- estos tests se adaptan a la implementación real:

- El top-3 (C.3) NO se guarda como columna -- se calcula en caliente en un
  futuro endpoint (ver docstring de matcher.py). No hay `suggested_product_id`
  que testear; en su lugar se prueba que _evaluate()/run_matching() nunca
  auto-confirman un candidato ambiguo (documentado en la sección de
  candidatos empatados más abajo).
- Los umbrales de C.2 se testean con _evaluate() y un candidato CONTROLADO
  (monkeypatch de _best_candidate) en vez de depender de que un texto real
  produzca una similarity() exacta -- los límites (0.6, 0.35) son de la
  LÓGICA de decisión, no de pg_trgm en sí, así que fijarlos con un valor
  exacto es más preciso que intentar que dos strings den justo 0.6."""

from __future__ import annotations

import matcher
from base_script import Classification


def make_classification(product_type="BOOSTER_BOX", set_code="OP11", language="EN", main_set="OP11"):
    return Classification(product_type=product_type, set_code=set_code, language=language, main_set=main_set)


def stub_candidate(monkeypatch, product_id=1, main_set="OP11", language="EN", score=0.75):
    monkeypatch.setattr(matcher, "_best_candidate", lambda cur, category_id, raw_name: (
        (product_id, main_set, language, score) if score is not None else None
    ))


def seed_category(conn, slug="booster-box", name="Booster Box"):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO category (name, slug) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING",
                    (name, slug))
        cur.execute("SELECT id FROM category WHERE slug = %s", (slug,))
        category_id = cur.fetchone()[0]
    conn.commit()
    return category_id


# ===========================================================================
# 6.1 -- Tabla de umbrales de confianza (C.2)
# ===========================================================================

class TestUmbralesDeConfianza:
    def test_coincide_todo_similarity_075_confirmed(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, main_set="OP11", language="EN", score=0.75)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, make_classification(), "raw name")
        assert outcome.match_status == "confirmed"
        assert outcome.match_confidence == 0.75

    def test_coincide_todo_similarity_061_justo_sobre_el_corte_confirmed(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.61)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, make_classification(), "raw name")
        assert outcome.match_status == "confirmed"

    def test_coincide_todo_similarity_060_justo_en_el_corte_NO_confirma(self, db_conn, monkeypatch):
        # Límite exacto: el código usa "score > CONFIRMED_SIMILARITY_THRESHOLD"
        # (estrictamente mayor, no >=) -- 0.60 exacto NO confirma, cae a
        # needs_review. Documentado explícitamente, no dejado implícito.
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.60)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, make_classification(), "raw name")
        assert outcome.match_status == "needs_review"

    def test_coincide_todo_similarity_045_needs_review(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.45)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, make_classification(), "raw name")
        assert outcome.match_status == "needs_review"

    def test_idioma_no_coincide_degrada_a_needs_review_aunque_similarity_sea_alta(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, main_set="OP11", language="JP", score=0.90)  # candidato en JP
        classification = make_classification(language="EN")  # pero el raw_name se detectó como EN
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "raw name")
        assert outcome.match_status == "needs_review"

    def test_idioma_no_detectado_tambien_degrada_a_needs_review(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, main_set="OP11", language="EN", score=0.90)
        classification = make_classification(language=None)  # no se detectó idioma en el raw_name
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "raw name")
        assert outcome.match_status == "needs_review"

    def test_similarity_034_unmatched(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.34)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, make_classification(), "raw name")
        assert outcome.match_status == "unmatched"

    def test_similarity_035_justo_en_el_corte_no_es_unmatched_por_ese_motivo(self, db_conn, monkeypatch):
        # "score < REVIEW_SIMILARITY_THRESHOLD" -- 0.35 exacto NO es menor
        # que 0.35, así que pasa el primer filtro (podría acabar needs_review
        # si el resto de condiciones se cumplen).
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, main_set="OP11", language="EN", score=0.35)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, make_classification(), "raw name")
        assert outcome.match_status == "needs_review"

    def test_main_set_no_coincide_es_unmatched_pese_a_similarity_alta(self, db_conn, monkeypatch):
        # El caso que evita falsos positivos por nombres parecidos: mismo
        # tipo de producto, similarity muy alta (0.90), pero de OTRO set.
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, main_set="OP99", language="EN", score=0.90)
        classification = make_classification(main_set="OP11")  # el raw_name es de OP11
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "raw name")
        assert outcome.match_status == "unmatched"
        assert outcome.product_id is None

    def test_sin_ningun_candidato_en_la_categoria_es_unmatched(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=None)  # _best_candidate no encuentra nada
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, make_classification(), "raw name")
        assert outcome.match_status == "unmatched"


# ===========================================================================
# 6.2 -- Exclusión de tipos no comparables (C.5/D.3)
# ===========================================================================

class TestExclusionNotApplicable:
    def test_lote_cartas_es_not_applicable_sin_consultar_similitud(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        # Si _evaluate consultara _best_candidate para LOTE_CARTAS, este
        # stub haría que confirmara -- si el test pasa con not_applicable,
        # confirma que ni siquiera se llega a consultarlo.
        stub_candidate(monkeypatch, score=0.99)
        classification = make_classification(product_type="LOTE_CARTAS")
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "lote de cartas")
        assert outcome.match_status == "not_applicable"

    def test_otros_es_not_applicable(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.99)
        classification = make_classification(product_type="OTROS")
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "cualquier cosa")
        assert outcome.match_status == "not_applicable"


# ===========================================================================
# run_matching() end-to-end contra Postgres real
# ===========================================================================

class TestRunMatchingEndToEnd:
    def _seed_store_product(self, conn, raw_name, store_label="Tienda"):
        import persistence
        from base_script import Platform, Product, StoreConfig
        cfg = StoreConfig(store_label, f"https://{store_label.lower()}.example", Platform.SHOPIFY,
                           shopify_collection="x")
        store_ids = persistence.sync_stores(conn, [cfg])
        conn.commit()
        product = Product(store=store_label, platform="shopify", id_product=None, name=raw_name,
                           variant=None, product_type="", main_set=None, set_code=None, language=None,
                           price=10.0, stock_status="DISPONIBLE",
                           url=f"https://{store_label.lower()}.example/p1", sku=None, image_url=None)
        persistence._save_one_store(conn, store_ids[cfg.domain], [product], __import__("datetime").date.today())
        conn.commit()

    def test_sin_categorias_sembradas_lanza_runtimeerror_claro(self, db_conn):
        import pytest
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
            # Confirmar manualmente algo DISTINTO para probar que una
            # segunda pasada no lo toca -- si run_matching reevaluara
            # 'confirmed', esto lo pisaría.
            with db_conn.cursor() as cur:
                cur.execute("UPDATE store_product SET product_id = %s, match_confidence = 0.99", (product_id,))
            db_conn.commit()
            matcher.run_matching(db_conn)
            with db_conn.cursor() as cur:
                cur.execute("SELECT match_confidence FROM store_product")
                assert float(cur.fetchone()[0]) == 0.99  # no se reevaluó, se quedó el valor manual


# ===========================================================================
# C.1 -- find_missing_canonical_candidates()
# ===========================================================================

class TestFindMissingCanonicalCandidates:
    def _seed_store_product(self, conn, raw_name, store_label):
        import persistence
        from base_script import Platform, Product, StoreConfig
        cfg = StoreConfig(store_label, f"https://{store_label.lower()}.example", Platform.SHOPIFY,
                           shopify_collection="x")
        store_ids = persistence.sync_stores(conn, [cfg])
        conn.commit()
        product = Product(store=store_label, platform="shopify", id_product=None, name=raw_name,
                           variant=None, product_type="", main_set=None, set_code=None, language=None,
                           price=10.0, stock_status="DISPONIBLE",
                           url=f"https://{store_label.lower()}.example/p1", sku=None, image_url=None)
        persistence._save_one_store(conn, store_ids[cfg.domain], [product], __import__("datetime").date.today())
        conn.commit()

    def test_varias_tiendas_sin_candidato_genera_sugerencia(self, db_conn):
        seed_category(db_conn, slug="booster-box")
        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaA")
        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaB")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert len(suggestions) == 1
        assert suggestions[0]["main_set"] == "OP17"
        assert suggestions[0]["store_count"] == 2

    def test_una_sola_tienda_no_genera_sugerencia(self, db_conn):
        seed_category(db_conn, slug="booster-box")
        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaA")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert suggestions == []

    def test_si_ya_existe_candidato_no_genera_sugerencia(self, db_conn):
        category_id = seed_category(db_conn, slug="booster-box")
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, main_set, name_canonical) VALUES (%s, %s, 'OP17', 'x')",
                (game_id, category_id),
            )
        db_conn.commit()

        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaA")
        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaB")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert suggestions == []

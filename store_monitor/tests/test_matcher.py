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

import pytest

import matcher
from shared.classify import cantidad_es_ambigua, classify_with_category
from shared.domain import Classification


def make_classification(product_type="BOOSTER_BOX", set_code="OP11", language="EN", main_set="OP11"):
    return Classification(product_type=product_type, set_code=set_code, language=language, main_set=main_set)


def stub_candidate(monkeypatch, product_id=1, set_code="OP11", language="EN", score=0.75, es_fallback=False):
    def _fake_best_candidate(cur, category_id, raw_name, set_code=None, language=None):
        # set_code/language de aquí son los BUSCADOS (lo que _evaluate le
        # pasa) -- se ignoran a propósito, el stub siempre devuelve el
        # candidato fijo configurado arriba, sea cual sea la búsqueda.
        candidate = (product_id, candidate_set_code, candidate_language, score) if score is not None else None
        return candidate, es_fallback

    candidate_set_code = set_code
    candidate_language = language
    monkeypatch.setattr(matcher, "_best_candidate", _fake_best_candidate)


def seed_category(conn, slug="booster-box", name="Booster Box"):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO category (name, slug) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING",
                    (name, slug))
        cur.execute("SELECT id FROM category WHERE slug = %s", (slug,))
        category_id = cur.fetchone()[0]
    conn.commit()
    return category_id


def seed_canonical(conn, category_id, name_canonical, set_code, language="EN"):
    """Producto canónico real (no stub) -- para tests que necesitan
    similarity() de pg_trgm de verdad, contra category_id concretos."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                    "ON CONFLICT (slug) DO NOTHING")
        cur.execute("SELECT id FROM game WHERE slug='one-piece'")
        game_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO product (game_id, category_id, set_code, language, name_canonical) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (game_id, category_id, set_code, language, name_canonical),
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
# 6.1 -- Tabla de umbrales de confianza (C.2)
# ===========================================================================

class TestUmbralesDeConfianza:
    """Camino DE SIEMPRE (legado, pre-2026-08-27): candidato sin las cinco
    condiciones del auto-confirmado por set_code (aquí, forzado con
    classification.set_code=None -- "no se detectó código en el raw_name",
    el caso real en el que este camino sigue siendo el único disponible).
    Los umbrales de score (0.6/0.35) solo gobiernan ESTE camino -- ver
    TestAutoConfirmadoPorSetCode para el camino rápido, que ya no depende
    del score en absoluto (implementacion-auto-confirmado-setcode.md 1.5)."""

    def test_coincide_todo_similarity_075_confirmed(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", score=0.75)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "booster-box", "raw name")
        assert outcome.match_status == "confirmed"
        assert outcome.match_confidence == 0.75

    def test_coincide_todo_similarity_061_justo_sobre_el_corte_confirmed(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.61)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "booster-box", "raw name")
        assert outcome.match_status == "confirmed"

    def test_coincide_todo_similarity_060_justo_en_el_corte_NO_confirma(self, db_conn, monkeypatch):
        # Límite exacto: el código usa "score > CONFIRMED_SIMILARITY_THRESHOLD"
        # (estrictamente mayor, no >=) -- 0.60 exacto NO confirma, cae a
        # needs_review. Documentado explícitamente, no dejado implícito.
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.60)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "booster-box", "raw name")
        assert outcome.match_status == "needs_review"

    def test_coincide_todo_similarity_045_needs_review(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.45)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "booster-box", "raw name")
        assert outcome.match_status == "needs_review"

    def test_idioma_no_coincide_degrada_a_needs_review_aunque_similarity_sea_alta(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="JP", score=0.90)  # candidato en JP
        classification = make_classification(language="EN")  # pero el raw_name se detectó como EN
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "booster-box", "raw name")
        assert outcome.match_status == "needs_review"

    def test_idioma_no_detectado_tambien_degrada_a_needs_review(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", score=0.90)
        classification = make_classification(language=None)  # no se detectó idioma en el raw_name
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "booster-box", "raw name")
        assert outcome.match_status == "needs_review"

    def test_similarity_034_unmatched(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.34)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "booster-box", "raw name")
        assert outcome.match_status == "unmatched"

    def test_similarity_035_justo_en_el_corte_no_es_unmatched_por_ese_motivo(self, db_conn, monkeypatch):
        # "score < REVIEW_SIMILARITY_THRESHOLD" -- 0.35 exacto NO es menor
        # que 0.35, así que pasa el primer filtro (podría acabar needs_review
        # si el resto de condiciones se cumplen).
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", score=0.35)
        classification = make_classification(set_code=None)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "booster-box", "raw name")
        assert outcome.match_status == "needs_review"

    def test_set_code_no_coincide_es_unmatched_pese_a_similarity_alta(self, db_conn, monkeypatch):
        # El caso que evita falsos positivos por nombres parecidos: mismo
        # tipo de producto, similarity muy alta (0.90), pero de OTRO set/código
        # -- caso real encontrado revisando la cola (2026-08-27): "Starter
        # Deck ONE PIECE FILM edition ST-05" ganaba por similitud de texto a
        # variantes de OTRO código (ST-30 vs ST-31, etc).
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP99", language="EN", score=0.90)
        classification = make_classification(set_code="OP11")  # el raw_name es de OP11
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "booster-box", "raw name")
        assert outcome.match_status == "unmatched"
        assert outcome.product_id is None

    def test_set_code_solo_en_un_lado_no_rechaza_por_ese_motivo(self, db_conn, monkeypatch):
        # Regresión real encontrada simulando el impacto contra datos reales
        # (2026-08-27): Illustration Box/Devil Fruits Collection nunca
        # tienen set_code poblado en el catálogo (sembrados desde "Vol.N",
        # sin código en mayúsculas) -- si la tienda SÍ trae un código
        # reconocible ("IB-06"), comparar a secas ("IB06" != None)
        # rechazaba de más. Que falte el dato en UN lado no es evidencia de
        # que sea otro lanzamiento -- solo lo es si AMBOS lados lo traen y
        # no coincide (ver el test de arriba).
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code=None, language="EN", score=0.75)  # candidato sin set_code
        classification = make_classification(set_code="IB06")  # pero el raw_name sí trae uno
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, "booster-box", "raw name")
        assert outcome.match_status == "confirmed"  # sigue el flujo normal de umbrales, no se rechaza

    def test_sin_ningun_candidato_en_la_categoria_es_unmatched(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=None)  # _best_candidate no encuentra nada
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, make_classification(), "booster-box", "raw name")
        assert outcome.match_status == "unmatched"


# ===========================================================================
# Camino RÁPIDO -- auto-confirmado por set_code exacto, las cinco
# condiciones de implementacion-auto-confirmado-setcode.md 1.5. No depende
# del score de similitud: candidato PRIMARIO (no fallback) + set_code +
# idioma + cantidad no ambigua basta, aunque el texto se parezca poco.
# ===========================================================================

class TestAutoConfirmadoPorSetCode:
    def test_cinco_condiciones_confirman_aunque_la_similitud_sea_bajisima(self, db_conn, monkeypatch):
        # El punto central de 1.5: a diferencia del camino de siempre
        # (TestUmbralesDeConfianza), aquí NO hay umbral de score -- un
        # score de 0.05 confirma igual si las otras cuatro condiciones
        # se cumplen.
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", score=0.05, es_fallback=False)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, make_classification(), "booster-box", "raw name")
        assert outcome.match_status == "confirmed"
        assert outcome.match_confidence == 0.05

    def test_candidato_por_fallback_no_confirma_aunque_todo_lo_demas_coincida(self, db_conn, monkeypatch):
        # es_fallback=True (candidato de la búsqueda cross-categoría, ver
        # _best_candidate) bloquea el camino rápido -- score bajo a
        # propósito para no colar por el camino de siempre tampoco (ver
        # comentario de _evaluate sobre ese "hueco" del score alto).
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, set_code="OP11", language="EN", score=0.20, es_fallback=True)
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, make_classification(), "booster-box", "raw name")
        assert outcome.match_status == "needs_review"

    def test_cantidad_ambigua_bloquea_el_camino_rapido(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn, slug="booster-box")
        stub_candidate(monkeypatch, set_code="OP11", language="EN", score=0.20, es_fallback=False)
        classification = make_classification(set_code="OP11")
        with db_conn.cursor() as cur:
            # "x12" en un booster-box (estándar 24) -- cantidad_es_ambigua()
            # devuelve True, bloquea las cinco condiciones.
            outcome = matcher._evaluate(
                cur, {"booster-box": category_id}, classification, "booster-box", "OP11 Caja de sobres x12",
            )
        assert outcome.match_status == "needs_review"


# ===========================================================================
# _best_candidate -- set_code exacto gana a similitud pura (contra Postgres
# real: hace falta similarity() de pg_trgm de verdad, no un stub)
# ===========================================================================

class TestBestCandidatePrioridadDeSetCode:
    def _seed_product(self, conn, category_id, name_canonical, set_code):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, name_canonical) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (game_id, category_id, set_code, name_canonical),
            )
            return cur.fetchone()[0]

    def test_set_code_exacto_gana_aunque_tenga_menos_similitud_de_texto(self, db_conn):
        # Caso real encontrado revisando la cola de Arte9 (2026-08-27): el
        # texto genérico "ST-05" comparte más palabras con el raw_name
        # ("Starter Deck", "ONE PIECE") que el candidato realmente correcto
        # (ST-31), así que gana en similarity() pura -- pero NO es el mismo
        # producto. set_code debe desempatar a favor del código correcto.
        category_id = seed_category(db_conn)
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
        assert es_fallback is False  # dentro de su propia categoría, no es un candidato de fallback

    def test_sin_set_code_en_el_raw_name_se_queda_con_similitud_pura(self, db_conn):
        # Degradación segura: si el raw_name no trae código reconocible
        # (set_code=None), el comportamiento es el de siempre -- top-1 por
        # similarity(), sin preferencia artificial por ningún candidato.
        category_id = seed_category(db_conn)
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

    def test_caja_vs_sobre_con_mismo_set_code_desempata_por_variante(self, db_conn):
        # Caso real encontrado revisando la cola (2026-08-27): "Premium
        # Booster" (sobre) y "Premium Booster Box" (caja) del mismo PRB-02
        # conviven en la MISMA categoría con el MISMO set_code -- ese
        # desempate no distingue cuál es cuál, hace falta is_box_variant().
        category_id = seed_category(db_conn, slug="premium-collection")
        sobre_id = self._seed_product(
            db_conn, category_id, "Premium Booster: One Piece Card The Best vol.2 PRB-02 EN", "PRB02",
        )
        caja_id = self._seed_product(
            db_conn, category_id, "Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", "PRB02",
        )

        with db_conn.cursor() as cur:
            candidato_para_caja, fallback_caja = matcher._best_candidate(
                cur, category_id, "CAJA THE BEST VOL.2 – PRB-02 – ONE PIECE", set_code="PRB02",
            )
            candidato_para_sobre, fallback_sobre = matcher._best_candidate(
                cur, category_id, "SOBRE THE BEST VOL.2 – PRB-02 – ONE PIECE", set_code="PRB02",
            )

        assert candidato_para_caja[0] == caja_id
        assert candidato_para_sobre[0] == sobre_id
        assert fallback_caja is False and fallback_sobre is False

    def test_fallback_cross_categoria_por_set_code_exacto(self, db_conn):
        # Caso real (2026-08-27): el canónico PRB02 "The Best vol.2" se
        # sembró en premium-collection, pero varias tiendas lo listan sin
        # la palabra "Premium" ("Caja PRB02") -- classify_product() lo
        # clasifica entonces como BOOSTER_BOX, categoría vacía para ese
        # set_code. Sin el fallback, _best_candidate() nunca encontraría el
        # canónico real por buscar solo dentro de booster-box.
        booster_box_category_id = seed_category(db_conn, slug="booster-box")
        premium_category_id = seed_category(db_conn, slug="premium-collection")
        prb_id = self._seed_product(
            db_conn, premium_category_id, "Premium Booster Box: One Piece Card The Best vol.2 PRB-02 EN", "PRB02",
        )

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(
                cur, booster_box_category_id, "One Piece Card Game Premium Booster PRB-02 Caja", set_code="PRB02",
            )

        assert candidate is not None
        assert candidate[0] == prb_id
        assert es_fallback is True  # vino de la búsqueda cross-categoría, no de booster-box

    def test_sin_fallback_si_la_categoria_derivada_ya_trae_el_set_code_exacto(self, db_conn):
        # El fallback NO debe dispararse si ya hay un candidato correcto en
        # la categoría derivada -- evita una consulta extra innecesaria.
        category_id = seed_category(db_conn, slug="booster-box")
        correcto_id = self._seed_product(db_conn, category_id, "Booster Box OP17 EN", "OP17")

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(cur, category_id, "Booster Box OP17 EN", set_code="OP17")

        assert candidate[0] == correcto_id
        assert es_fallback is False

    def test_fallback_no_dispara_sin_set_code(self, db_conn):
        # Sin set_code detectado no hay señal fuerte con la que buscar
        # cross-categoría -- se queda con lo que haya (o nada) en la
        # categoría derivada, igual que antes de este cambio.
        category_id = seed_category(db_conn, slug="booster-box")

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(
                cur, category_id, "Producto sin código reconocible", set_code=None,
            )

        assert candidate is None
        assert es_fallback is False

    def test_idioma_desempata_cuando_en_y_jp_empatan_en_similitud(self, db_conn):
        # docs/pendientes-motor-matching.md punto 6 -- caso real encontrado
        # en una siembra completa contra Postgres real: "One Piece OP13
        # Carrying On His Will" daba EXACTAMENTE el mismo score de
        # similarity() contra el canónico EN y el JP (solo difieren en el
        # sufijo) -- sin el idioma como desempate, ORDER BY caía al orden
        # físico de la tabla y devolvía casi siempre el EN, aunque
        # classify_product() ya hubiera detectado JP. 61 filas reales se
        # quedaban en needs_review por esto.
        category_id = seed_category(db_conn, slug="booster-box")
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
        # Degradación segura: sin `language` (None), el criterio nuevo no
        # discrimina nada -- mismo comportamiento que antes de este cambio.
        category_id = seed_category(db_conn, slug="booster-box")
        seed_canonical(db_conn, category_id, "Booster Box: Carrying On His Will OP-13 EN", "OP13", "EN")
        seed_canonical(db_conn, category_id, "Booster Box: Carrying On His Will OP-13 JP", "OP13", "JP")

        with db_conn.cursor() as cur:
            candidate, _ = matcher._best_candidate(
                cur, category_id, "One Piece OP13 Carrying On His Will", set_code="OP13",
            )

        assert candidate is not None  # no revienta sin language, sigue devolviendo alguno de los dos

    def test_candidato_por_fallback_cross_categoria_se_marca_como_tal(self, db_conn):
        # implementacion-auto-confirmado-setcode.md 2.4 -- caso real: la
        # categoría promo-card se queda vacía a propósito (nadie ha sembrado
        # cartas promo todavía), el único candidato posible con set_code
        # OP13 vive en booster-pack. Debe marcarse como fallback.
        promo_card_category_id = seed_category(db_conn, slug="promo-card")
        booster_pack_category_id = seed_category(db_conn, slug="booster-pack")
        self._seed_product(db_conn, booster_pack_category_id, "Booster Pack OP-13 Carrying On His Will EN", "OP13")

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(
                cur, promo_card_category_id, "Carta Promo Sellada Ichiban Kuji Monkey D. Luffy OP13", set_code="OP13",
            )

        assert candidate is not None
        assert es_fallback is True

    def test_candidato_dentro_de_su_categoria_no_se_marca_como_fallback(self, db_conn):
        category_id = seed_category(db_conn, slug="booster-box")
        correcto_id = self._seed_product(db_conn, category_id, "Caja OP16 The Time of Battle EN", "OP16")

        with db_conn.cursor() as cur:
            candidate, es_fallback = matcher._best_candidate(cur, category_id, "Caja OP16 ...", set_code="OP16")

        assert candidate[0] == correcto_id
        assert es_fallback is False


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
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, None, "lote de cartas")
        assert outcome.match_status == "not_applicable"

    def test_otros_es_not_applicable(self, db_conn, monkeypatch):
        category_id = seed_category(db_conn)
        stub_candidate(monkeypatch, score=0.99)
        classification = make_classification(product_type="OTROS")
        with db_conn.cursor() as cur:
            outcome = matcher._evaluate(cur, {"booster-box": category_id}, classification, None, "cualquier cosa")
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

    def test_run_matching_lee_raw_tags_de_bbdd_y_lo_usa_para_clasificar(self, db_conn):
        # Integración completa (2026-08-27): raw_name a secas no trae
        # ninguna palabra de tipo (quedaría OTROS/not_applicable) -- solo
        # con las tags guardadas en BBDD (persistence -> raw_tags ->
        # run_matching las lee -> classify_with_category las usa) llega a
        # needs_review con un candidato real.
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
            db_conn, "One Piece OP13 Carrying On His Will",
            tags="Caja One Piece, Cajas, Cajas de Sobres, OP-13 Carrying On His Will",
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

    def test_agrupa_usando_raw_tags_cuando_name_solo_no_basta(self, db_conn):
        # Sin tags, "One Piece OP17 The World's Strongest Warriors" no
        # tiene ninguna palabra de tipo -- clasificaría OTROS y ni
        # aparecería en la agrupación (OTROS está en NOT_APPLICABLE).
        seed_category(db_conn, slug="booster-box")
        self._seed_store_product(
            db_conn, "One Piece OP17 The World's Strongest Warriors", "TiendaA",
            tags="Caja One Piece, Cajas, Cajas de Sobres",
        )
        self._seed_store_product(
            db_conn, "One Piece OP17 The World's Strongest Warriors", "TiendaB",
            tags="Caja One Piece, Cajas, Cajas de Sobres",
        )

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert len(suggestions) == 1
        assert suggestions[0]["main_set"] == "OP17"
        assert suggestions[0]["store_count"] == 2

    def test_si_ya_existe_candidato_no_genera_sugerencia(self, db_conn):
        category_id = seed_category(db_conn, slug="booster-box")
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, main_set, name_canonical) "
                "VALUES (%s, %s, 'OP17', 'OP17', 'x')",
                (game_id, category_id),
            )
        db_conn.commit()

        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaA")
        self._seed_store_product(db_conn, "Booster Box OP17 EN", "TiendaB")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert suggestions == []

    def test_candidato_con_main_set_pero_set_code_null_no_evita_la_sugerencia(self, db_conn):
        # Regresión (2026-08-27): Double Pack/Illustration Box tienen
        # set_code propio y main_set=NULL en la BBDD real -- un candidato
        # con main_set poblado pero set_code NULL no debe contar como "ya
        # existe" para un grupo con set_code distinto.
        category_id = seed_category(db_conn, slug="booster-box")
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
        # Caso real PRB02 (2026-08-27): el canónico vive en
        # premium-collection, pero las tiendas que no dicen "Premium" se
        # clasifican como BOOSTER_BOX -- sin el fallback cross-categoría,
        # esto aparecería como "falta sembrar" pese a que el producto ya
        # existe con ese set_code exacto, solo que en otra categoría.
        booster_box_id = seed_category(db_conn, slug="booster-box")
        premium_id = seed_category(db_conn, slug="premium-collection")
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                        "ON CONFLICT (slug) DO NOTHING")
            cur.execute("SELECT id FROM game WHERE slug='one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, name_canonical) "
                "VALUES (%s, %s, 'PRB02', 'Premium Booster Box: The Best vol.2 PRB-02 EN')",
                (game_id, premium_id),
            )
        db_conn.commit()
        assert booster_box_id  # categoría creada para que classify_with_category la resuelva

        self._seed_store_product(db_conn, "One Piece Card Game Premium Booster PRB-02 Caja", "TiendaA")
        self._seed_store_product(db_conn, "One Piece Card Game Premium Booster PRB-02 Caja", "TiendaB")

        suggestions = matcher.find_missing_canonical_candidates(db_conn, min_stores=2)

        assert suggestions == []


# ===========================================================================
# implementacion-auto-confirmado-setcode.md 2.3 -- cantidad_es_ambigua(),
# unitarios directos, sin necesitar BBDD.
# ===========================================================================

class TestCantidadEsAmbigua:
    @pytest.mark.parametrize("raw_name,category_slug,esperado", [
        ("Caja de 24 Sobres Royal Blood OP10 - Inglés", "booster-box", False),
        ("Caja de 20 Sobres The Best 2 PRB02 - Inglés", "premium-collection", False),
        ("[INGLÉS] One Piece Card Game OP-16 Caja de sobres x12", "booster-box", True),
        ("Pack 5 Sobres One Piece Adventure on KAMI's Island OP15 - Japones", "booster-pack", True),
        ("[INGLÉS] One Piece Card Game Starter Deck EX Gear5 [ST21] x6", "starter-deck", True),
        (
            "One Piece Card Game Double Pack Set Vol.10 [DP-10] – 2 Booster Packs + Exclusive DON!! Card",
            "double-pack", False,
        ),  # "2 Booster Packs" describe el contenido normal de un Double Pack, no un bundle de 2 sets
    ])
    def test_cantidad_es_ambigua_casos_reales(self, raw_name, category_slug, esperado):
        assert cantidad_es_ambigua(raw_name, category_slug) == esperado

    def test_categoria_no_reconocida_nunca_es_ambigua(self):
        # Ni en _CANTIDAD_ESTANDAR_POR_CATEGORIA ni en _CATEGORIAS_UNIDAD_UNICA
        # -- no se arriesga un falso positivo por exceso de celo.
        assert cantidad_es_ambigua("Pack 5 Sobres de algo", "booster-case") is False


# ===========================================================================
# implementacion-auto-confirmado-setcode.md 2.1 -- control positivo: 8 casos
# reales de multi_tienda_one_piece.csv revisados a mano (2026-08-27), todos
# confirmados como el producto correcto. Regresión: si alguno deja de
# confirmar, algo del cambio rompió el camino feliz.
# ===========================================================================

class TestControlPositivoCasosReales:
    @pytest.mark.parametrize("raw_name,raw_variant,category_slug,set_code,canonical_name", [
        (
            "One Piece: Double Pack Set Display DP-11", None, "double-pack", "DP11",
            "One Piece Double Pack Set Vol.11 DP-11 EN",
        ),
        (
            "One Piece Card Game Playmat Limited Edition Vol 2", None, "playmat", "VOL02",
            "Playmat Vol.2 EN",
        ),
        (
            "One Piece | Illustration Box Vol.4 Perona & Mihawk", "Inglés", "illustration-box", "VOL04",
            "Illustration Box Vol.4 Perona & Mihawk EN",
        ),
        (
            "One Piece Card Game - Devil Fruits Collection Vol.3 Op-Op Fruit (DF03)", None,
            "devil-fruits-collection", "DF03", "Devil Fruits Collection Vol.3 Op-Op Fruit DF03 EN",
        ),
        (
            "Caja de 20 Sobres The Best 2 PRB02 - Inglés", None, "premium-collection", "PRB02",
            "Caja de 20 Sobres The Best Vol.2 PRB-02 EN",
        ),
        (
            "One Piece Card Game - Gear 5 Starter Deck EX ST21", None, "starter-deck", "ST21",
            "Starter Deck EX Gear 5 ST-21 EN",
        ),
        (
            "Caja sobres One Piece OP-16 The Time of Battle (inglés)", None, "booster-box", "OP16",
            "Caja de Sobres One Piece OP-16 The Time of Battle EN",
        ),
        (
            "ONE PIECE TCG - EB-05", None, "booster-pack", "EB05",
            "Booster Pack EB-05 One Piece TCG EN",
        ),
    ])
    def test_setcode_exacto_confirma_casos_reales_verificados_a_mano(
        self, db_conn, raw_name, raw_variant, category_slug, set_code, canonical_name,
    ):
        category_id = seed_category(db_conn, slug=category_slug, name=category_slug)
        seed_canonical(db_conn, category_id, canonical_name, set_code, language="EN")

        outcome = evaluar(db_conn, raw_name, raw_variant)

        assert outcome.match_status == "confirmed", (raw_name, outcome)


# ===========================================================================
# implementacion-auto-confirmado-setcode.md 2.2 -- falsos positivos a
# evitar: casos reales de multi_tienda_one_piece.csv que comparten set_code
# (o casi) con un candidato real y que, sin las guardas de este cambio,
# confirmarían incorrectamente.
# ===========================================================================

class TestFalsosPositivosCasosReales:
    def test_promo_card_no_confirma_por_fallback_cross_categoria(self, db_conn):
        # cross_categoria: promo-card está vacía, el candidato solo aparece
        # por el fallback de set_code en todo el catálogo (Booster Pack
        # OP-13) -- NO es el mismo producto.
        seed_category(db_conn, slug="promo-card")
        booster_pack_id = seed_category(db_conn, slug="booster-pack")
        seed_canonical(db_conn, booster_pack_id, "Booster Pack OP-13 Carrying On His Will EN", "OP13", "EN")

        outcome = evaluar(db_conn, "Carta Promo Sellada Ichiban Kuji Monkey D. Luffy OP13 - Japones")

        assert outcome.match_status != "confirmed"

    def test_case_no_confirma_contra_premium_booster_box(self, db_conn):
        # cantidad_ambigua: es un Case (10 cajas), no una caja suelta --
        # se clasifica BOOSTER_CASE, no debe confirmar contra Premium
        # Booster Box (llega solo por el fallback cross-categoría).
        seed_category(db_conn, slug="booster-case")
        premium_id = seed_category(db_conn, slug="premium-collection")
        seed_canonical(db_conn, premium_id, "Caja de 20 Sobres The Best Vol.2 PRB-02 EN", "PRB02", "EN")

        outcome = evaluar(db_conn, "(CASE) THE BEST 2 – PRB-02 – x10 Booster Box- One Piece Card Game")

        assert outcome.match_status != "confirmed"

    def test_pack_5_sobres_no_confirma_contra_booster_pack_suelto(self, db_conn):
        # cantidad_ambigua: bundle real de 5 sobres, booster-pack es
        # categoría de unidad única -- 5 != 1.
        booster_pack_id = seed_category(db_conn, slug="booster-pack")
        seed_canonical(db_conn, booster_pack_id, "Booster Pack OP-15 Adventure on KAMI's Island EN", "OP15", "EN")

        outcome = evaluar(db_conn, "Pack 5 Sobres One Piece Adventure on KAMI’s Island OP15 - Japones")

        assert outcome.match_status != "confirmed"

    def test_caja_x12_no_confirma_contra_booster_box_de_24(self, db_conn):
        # cantidad_ambigua: booster-box espera 24, x12 no coincide --
        # podría ser una caja distinta, no confiar solo en el set_code.
        booster_box_id = seed_category(db_conn, slug="booster-box")
        seed_canonical(db_conn, booster_box_id, "Caja de Sobres One Piece OP-16 The Time of Battle EN", "OP16", "EN")

        outcome = evaluar(db_conn, "[INGLÉS] One Piece Card Game OP-16 Caja de sobres x12")

        assert outcome.match_status != "confirmed"

    def test_starter_deck_x6_no_confirma_contra_mazo_suelto(self, db_conn):
        # cantidad_ambigua: starter-deck es categoría de unidad única --
        # 6 != 1.
        starter_deck_id = seed_category(db_conn, slug="starter-deck")
        seed_canonical(db_conn, starter_deck_id, "Starter Deck EX Gear 5 ST-21 EN", "ST21", "EN")

        outcome = evaluar(db_conn, "[INGLÉS] One Piece Card Game Starter Deck EX Gear5 [ST21] x6")

        assert outcome.match_status != "confirmed"

    def test_idioma_jp_no_confirma_contra_booster_pack_en(self, db_conn):
        # idioma_no_coincide: raw es JP, el único candidato con ese set_code
        # en booster-pack es EN -- no existe canónico JP para este caso
        # concreto todavía.
        booster_pack_id = seed_category(db_conn, slug="booster-pack")
        seed_canonical(db_conn, booster_pack_id, "Booster Pack OP-03 Pillars of Strength EN", "OP03", "EN")

        outcome = evaluar(db_conn, "One Piece | Sobres OP-03 Pillars of Strength", "Japonés")

        assert outcome.match_status != "confirmed"

    def test_idioma_jp_no_confirma_contra_premium_collection_en(self, db_conn):
        # idioma_no_coincide: mismo caso, premium-collection no tiene
        # variante JP sembrada (a diferencia de booster-box/pack).
        premium_id = seed_category(db_conn, slug="premium-collection")
        seed_canonical(db_conn, premium_id, "Caja de 20 Sobres The Best Vol.2 PRB-02 EN", "PRB02", "EN")

        outcome = evaluar(db_conn, "Caja One Piece The Best 2 PRB02 - Japones")

        assert outcome.match_status != "confirmed"

    def test_setcode_inexistente_no_confirma_contra_otro_lanzamiento(self, db_conn):
        # setcode_distinto: OP-18 no existe en el catálogo sembrado
        # (lanzamiento posterior) -- NUNCA debe confirmar contra OP-13 u
        # otro set solo porque el texto se parezca.
        booster_pack_id = seed_category(db_conn, slug="booster-pack")
        seed_canonical(db_conn, booster_pack_id, "Booster Pack OP-13 Carrying On His Will EN", "OP13", "EN")

        outcome = evaluar(db_conn, "One Piece Card Game OP-18 Booster Pack - English")

        assert outcome.match_status != "confirmed"
        assert outcome.product_id is None

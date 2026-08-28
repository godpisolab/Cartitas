"""Casos reales tomados literalmente de la cola de 'Pendientes' del panel de
gestor (revisión 2026-08-28, export Cardzone/Pokemillon) -- pensados para
comprobar el fix del desempate por idioma (ver docstring de
matcher._best_candidate) contra un catálogo canónico realista, no contra un
stub.

Cada raw_name/raw_variant es copia literal de la fila real que aparecía como
`needs_review` en el export. El catálogo se siembra con pares EN/JP casi
idénticos en texto (mismo patrón que el catálogo oficial real: "Booster
Box: <título> OP-NN EN"/"...JP"), que es justo la situación que antes hacía
que similarity() decidiera el idioma "a ciegas".

Con el fix: las filas de CAJA/SOBRE SUELTO sin cantidad ambigua deben pasar
a `confirmed` (el idioma ya no depende del azar del score de texto). Las
filas "Pack N Sobres" (bundle de varios sobres comparado contra un sobre
suelto) deben SEGUIR en `needs_review` -- eso lo bloquea
`cantidad_es_ambigua()`, una regla de negocio aparte, no el bug de idioma.
Y la carta promo Ichiban Kuji debe seguir en `needs_review` porque no existe
canónico real para ella (candidato solo por fallback cross-categoría)."""

from __future__ import annotations

import pytest

import matcher
from shared.classify import classify_with_category


@pytest.fixture
def catalogo_real(db_conn):
    """Siembra un subconjunto del catálogo canónico con el mismo patrón de
    nombres que el catálogo oficial real (ver seed_official_catalog.py):
    cada lanzamiento tipo booster se siembra dos veces (Box y Pack), una
    vez por idioma."""
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece','one-piece') "
                    "ON CONFLICT (slug) DO NOTHING")
        cur.execute("SELECT id FROM game WHERE slug='one-piece'")
        game_id = cur.fetchone()[0]

        def category(slug, name):
            cur.execute("INSERT INTO category (name, slug) VALUES (%s, %s) "
                        "ON CONFLICT (slug) DO NOTHING", (name, slug))
            cur.execute("SELECT id FROM category WHERE slug = %s", (slug,))
            return cur.fetchone()[0]

        def seed(category_id, name_canonical, set_code, language):
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, language, name_canonical) "
                "VALUES (%s, %s, %s, %s, %s)",
                (game_id, category_id, set_code, language, name_canonical),
            )

        booster_box = category("booster-box", "Booster Box")
        booster_pack = category("booster-pack", "Booster Pack")
        premium = category("premium-collection", "Premium Collection")
        promo = category("promo-card", "Promo Card")

        # Booster Box + Booster Pack, EN + JP, para cada set_code que
        # aparece en los casos reales de abajo.
        releases = [
            ("OP15", "Adventure on Kami's Island"),
            ("OP16", "The Time of Battle"),
            ("OP13", "Carrying On His Will"),
            ("OP11", "A Fist of Divine Speed"),
            ("OP08", "Two Legends"),
        ]
        for code, title in releases:
            for language in ("EN", "JP"):
                seed(booster_box, f"Booster Box: {title} OP-{code[2:]} {language}", code, language)
                seed(booster_pack, f"Booster Pack: {title} OP-{code[2:]} {language}", code, language)

        # Premium Collection (caja + sobre), EN + JP, PRB-01 y PRB-02.
        for code, title in [("PRB01", "One Piece Card The Best PRB-01"),
                             ("PRB02", "One Piece Card The Best vol.2 PRB-02")]:
            for language in ("EN", "JP"):
                seed(premium, f"Premium Booster Box: {title} {language}", code, language)
                seed(premium, f"Premium Booster: {title} {language}", code, language)

        db_conn.commit()

    return {
        "booster-box": booster_box, "booster-pack": booster_pack,
        "premium-collection": premium, "promo-card": promo,
    }


def _evaluar(db_conn, category_ids, raw_name, raw_variant=None):
    classification, category_slug = classify_with_category(raw_name, raw_variant)
    with db_conn.cursor() as cur:
        return matcher._evaluate(cur, category_ids, classification, category_slug, raw_name)


class TestCasosRealesQueAhoraSeConfirmanConElFix:
    """Filas reales de CAJA/SOBRE SUELTO (sin cantidad ambigua) que antes se
    quedaban en needs_review porque el candidato ganador por similarity()
    podía ser el idioma equivocado -- con el desempate por idioma, ganan
    las cinco condiciones del auto-confirmado por set_code."""

    def test_cardzone_caja_op15_embalaje_danado(self, db_conn, catalogo_real):
        outcome = _evaluar(
            db_conn, catalogo_real,
            "Caja One Piece Adventure on Kami´s Island OP15 - Japones \"Embalaje dañado\"",
        )
        assert outcome.match_status == "confirmed"
        assert outcome.product_id is not None

    def test_pokemillon_caja_24_sobres_op15(self, db_conn, catalogo_real):
        outcome = _evaluar(
            db_conn, catalogo_real,
            "One Piece Caja 24 Sobres OP15 Adventure on KAMI’s Island", "Japonés",
        )
        assert outcome.match_status == "confirmed"

    def test_pokemillon_caja_24_sobres_op08_pese_a_similarity_muy_baja(self, db_conn, catalogo_real):
        # Este es el caso más exigente: el nombre de tienda es tan genérico
        # ("Caja 24 Sobres OP08 Two Legends") que similarity() da ~0.26,
        # muy por debajo de CONFIRMED_SIMILARITY_THRESHOLD (0.6) -- confirma
        # igual porque pasa por el camino rápido (set_code + idioma exactos
        # + cantidad no ambigua), no por el score.
        outcome = _evaluar(
            db_conn, catalogo_real,
            "One Piece | Caja 24 Sobres OP08 Two Legends", "Sobre Japonés",
        )
        assert outcome.match_status == "confirmed"
        assert outcome.match_confidence < 0.6

    def test_pokemillon_premium_collection_prb01_caja(self, db_conn, catalogo_real):
        outcome = _evaluar(
            db_conn, catalogo_real,
            "One Piece | Caja 20 Sobres PRB01 The Best Premium Collection", "Caja Japonés",
        )
        assert outcome.match_status == "confirmed"

    def test_pokemillon_sobre_suelto_op13(self, db_conn, catalogo_real):
        outcome = _evaluar(db_conn, catalogo_real, "One Piece Sobre OP13 Carrying On His Will", "Japonés")
        assert outcome.match_status == "confirmed"


class TestCasosRealesQueSiguenEnRevisionTrasElFix:
    """El fix de idioma no toca la regla de cantidad ambigua ni el gate de
    fallback cross-categoría -- estas filas reales deben seguir pidiendo
    revisión manual, por motivos DISTINTOS al bug que arreglamos."""

    @pytest.mark.parametrize("raw_name", [
        "Pack 5 Sobres One Piece Adventure on KAMI’s Island OP15 - Japones",
        "Pack 5 Sobres One Piece The Time of Battle OP16 - Japones",
        "Pack 5 Sobres OP11 A Fist Divine Speed - Japones",
        "Pack 5 Sobres One Piece Carrying on His Will OP13 - Japones",
    ])
    def test_bundle_pack_n_sobres_sigue_pidiendo_revision_por_cantidad_ambigua(
        self, db_conn, catalogo_real, raw_name,
    ):
        # "Pack 5 Sobres" es un bundle de 5 sobres comparado contra el
        # canónico de UN sobre suelto -- cantidad_es_ambigua() lo bloquea
        # pase lo que pase con el idioma o el set_code.
        outcome = _evaluar(db_conn, catalogo_real, raw_name)
        assert outcome.match_status == "needs_review"

    def test_carta_promo_ichiban_kuji_sigue_pidiendo_revision_sin_canonico_real(self, db_conn, catalogo_real):
        # No existe (ni debería inventarse) un canónico de "Ichiban Kuji" en
        # el catálogo -- el único candidato posible viene del fallback
        # cross-categoría (booster-pack OP13), marcado es_fallback=True, que
        # las cinco condiciones exigen que NO sea el caso para confirmar.
        outcome = _evaluar(
            db_conn, catalogo_real,
            "Carta Promo Sellada Ichiban Kuji Monkey D. Luffy OP13 - Japones",
        )
        assert outcome.match_status == "needs_review"
        assert outcome.product_id is None

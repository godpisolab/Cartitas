"""Tests de seed_official_catalog.py -- sin cobertura hasta ahora (bloque E,
construido después del plan de pruebas original)."""

from __future__ import annotations

import json

import seed_official_catalog as soc


class TestBuildName:
    def test_con_codigo_simple(self):
        assert soc._build_name("Booster Pack: Romance Dawn", "OP-01") == "Booster Pack: Romance Dawn OP-01 EN"

    def test_sin_codigo(self):
        assert soc._build_name("Official Playmat", None) == "Official Playmat EN"

    def test_codigo_partido_por_barra_usa_solo_el_primero(self):
        # "OP-14 / EB-04 (parte 1)" -- solo el primer código real importa
        # para los regex de main_set/set_code de classify_product().
        assert soc._build_name("Booster Pack: The Azure Sea's Seven", "OP-14 / EB-04 (parte 1)") == \
            "Booster Pack: The Azure Sea's Seven OP-14 EN"

    def test_language_por_defecto_en(self):
        assert soc._build_name("Booster Pack: Romance Dawn", "OP-01").endswith(" EN")

    def test_language_explicito_jp(self):
        assert soc._build_name("Booster Pack: Romance Dawn", "OP-01", "JP") == \
            "Booster Pack: Romance Dawn OP-01 JP"


class TestToBoxVariant:
    def test_booster_pack_genera_booster_box(self):
        assert soc._to_box_variant("Booster Pack: Romance Dawn") == "Booster Box: Romance Dawn"

    def test_extra_booster_genera_extra_booster_box(self):
        assert soc._to_box_variant("Extra Booster: Memorial Collection") == "Extra Booster Box: Memorial Collection"

    def test_premium_booster_genera_premium_booster_box(self):
        assert soc._to_box_variant("Premium Booster: One Piece Card The Best") == \
            "Premium Booster Box: One Piece Card The Best"

    def test_producto_no_booster_devuelve_none(self):
        assert soc._to_box_variant("Starter Deck: Straw Hat Crew") is None

    def test_treasure_boosters_set_no_empieza_por_ningun_prefijo_devuelve_none(self):
        # Caso real: "Treasure Boosters Set" no sigue el patrón "Booster
        # Pack:"/"Extra Booster:"/"Premium Booster:" -- no se duplica.
        assert soc._to_box_variant("Treasure Boosters Set") is None


class TestToCaseVariant:
    def test_booster_pack_genera_booster_box_case_con_multiplicador(self):
        assert soc._to_case_variant("Booster Pack: Romance Dawn", 12) == "Booster Box Case: Romance Dawn (x12)"

    def test_premium_booster_genera_premium_booster_box_case(self):
        assert soc._to_case_variant("Premium Booster: One Piece Card The Best", 10) == \
            "Premium Booster Box Case: One Piece Card The Best (x10)"

    def test_producto_no_booster_devuelve_none(self):
        assert soc._to_case_variant("Starter Deck: Straw Hat Crew", 12) is None


def _write_catalog(tmp_path, products):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"products": products}), encoding="utf-8")
    return path


def _seed_game_and_categories(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece')")
        for slug, name in [("booster-box", "Booster Box"), ("booster-pack", "Booster Pack"),
                            ("starter-deck", "Starter Deck")]:
            cur.execute("INSERT INTO category (name, slug) VALUES (%s, %s)", (name, slug))
    conn.commit()


class TestSeedFromCatalog:
    def test_sin_game_one_piece_lanza_runtimeerror(self, db_conn, tmp_path):
        catalog_path = _write_catalog(tmp_path, [])
        import pytest
        with pytest.raises(RuntimeError, match="one-piece"):
            soc.seed_from_catalog(db_conn, catalog_path)

    def test_booster_pack_se_siembra_como_caja_y_sobre_cada_uno_en_y_jp(self, db_conn, tmp_path):
        # 2026-08-27: booster-box/booster-pack están en
        # _JP_VARIANT_CATEGORY_SLUGS -- cada release se siembra 4 veces
        # (Pack EN, Pack JP, Box EN, Box JP), no 2.
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Booster Pack: Romance Dawn", "code": "OP-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)

        assert len(result["inserted"]) == 4
        with db_conn.cursor() as cur:
            cur.execute("SELECT name_canonical, language FROM product ORDER BY name_canonical")
            rows = cur.fetchall()
        assert rows == [
            ("Booster Box: Romance Dawn OP-01 EN", "EN"),
            ("Booster Box: Romance Dawn OP-01 JP", "JP"),
            ("Booster Pack: Romance Dawn OP-01 EN", "EN"),
            ("Booster Pack: Romance Dawn OP-01 JP", "JP"),
        ]

    def test_booster_pack_con_booster_case_sembrado_genera_tambien_el_case_x12(self, db_conn, tmp_path):
        # docs/pendientes-motor-matching.md punto 3 -- con booster-case
        # sembrada, cada release booster genera TAMBIÉN su Case (EN+JP,
        # booster-case está en _JP_VARIANT_CATEGORY_SLUGS) -- 6 insertados,
        # no 4. Multiplicador x12 para booster-box (verificado en el CSV
        # real: todos los OP-NN/EB-NN vistos).
        _seed_game_and_categories(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO category (name, slug) VALUES ('Booster Case', 'booster-case')")
        db_conn.commit()
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Booster Pack: Romance Dawn", "code": "OP-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)

        assert len(result["inserted"]) == 6
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT p.name_canonical, p.language, c.slug FROM product p "
                "JOIN category c ON c.id = p.category_id WHERE p.name_canonical LIKE %s",
                ("%Case%",),
            )
            rows = dict((name, (language, slug)) for name, language, slug in cur.fetchall())
        assert rows == {
            "Booster Box Case: Romance Dawn (x12) OP-01 EN": ("EN", "booster-case"),
            "Booster Box Case: Romance Dawn (x12) OP-01 JP": ("JP", "booster-case"),
        }

    def test_premium_booster_con_booster_case_sembrado_genera_case_x10(self, db_conn, tmp_path):
        # Multiplicador DISTINTO para premium-collection (x10, verificado
        # contra el único PRB-NN real visto en el CSV: "(CASE) THE BEST 2
        # PRB-02 x10") -- no asume el mismo x12 que booster-box.
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece')")
            for slug, name in [("premium-collection", "Premium Collection"), ("booster-case", "Booster Case")]:
                cur.execute("INSERT INTO category (name, slug) VALUES (%s, %s)", (name, slug))
        db_conn.commit()
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Premium Booster: One Piece Card The Best vol.2", "code": "PRB-02"},
        ])
        soc.seed_from_catalog(db_conn, catalog_path)

        with db_conn.cursor() as cur:
            cur.execute("SELECT name_canonical FROM product WHERE name_canonical LIKE %s", ("%Case%",))
            names = {row[0] for row in cur.fetchall()}
        assert "Premium Booster Box Case: One Piece Card The Best vol.2 (x10) PRB-02 EN" in names

    def test_starter_deck_no_genera_case_sin_evidencia_real(self, db_conn, tmp_path):
        # starter-deck no está en _CASE_MULTIPLIER_BY_CATEGORY a propósito
        # -- ninguna mención real de "Starter Deck Case" en el CSV, no se
        # inventa un multiplicador sin dato (ver docstring del punto 3).
        _seed_game_and_categories(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO category (name, slug) VALUES ('Booster Case', 'booster-case')")
        db_conn.commit()
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Starter Deck: Straw Hat Crew", "code": "ST-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)

        # EN + JP (starter-deck sí está en _JP_VARIANT_CATEGORY_SLUGS,
        # punto 4), pero ningún Case -- eso es lo que prueba este test.
        assert len(result["inserted"]) == 2
        assert not any("Case" in name for name in result["inserted"])

    def test_starter_deck_ahora_genera_variante_jp(self, db_conn, tmp_path):
        # docs/propuesta-mejoras-matching-sesion.md punto 4 (decidido,
        # 2026-08-28): starter-deck se añadió a _JP_VARIANT_CATEGORY_SLUGS
        # -- EN + JP, 2 insertados (sin box-variant que duplicar, mismo
        # patrón que premium-collection/double-pack).
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Starter Deck: Straw Hat Crew", "code": "ST-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)
        assert len(result["inserted"]) == 2
        assert set(result["inserted"]) == {
            "Starter Deck: Straw Hat Crew ST-01 EN", "Starter Deck: Straw Hat Crew ST-01 JP",
        }

    def test_premium_collection_y_double_pack_ahora_generan_variante_jp(self, db_conn, tmp_path):
        # docs/pendientes-motor-matching.md punto 6 (decidido, 2026-08-28):
        # _JP_VARIANT_CATEGORY_SLUGS se amplió más allá de booster-box/pack.
        # Ninguna de las dos tiene box-variant que duplicar (_to_box_variant
        # solo dobla "Booster/Extra Booster/Premium Booster:") -- EN + JP,
        # 2 insertados cada una, no 4.
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece')")
            for slug, name in [("premium-collection", "Premium Collection"), ("double-pack", "Double Pack")]:
                cur.execute("INSERT INTO category (name, slug) VALUES (%s, %s)", (name, slug))
        db_conn.commit()
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Premium Card Collection -25th Edition-", "code": None},
            {"name": "Double Pack Set Vol.1", "code": "DP-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)

        assert len(result["inserted"]) == 4
        with db_conn.cursor() as cur:
            cur.execute("SELECT name_canonical, language FROM product ORDER BY name_canonical")
            rows = cur.fetchall()
        assert rows == [
            ("Double Pack Set Vol.1 DP-01 EN", "EN"),
            ("Double Pack Set Vol.1 DP-01 JP", "JP"),
            ("Premium Card Collection -25th Edition- EN", "EN"),
            ("Premium Card Collection -25th Edition- JP", "JP"),
        ]

    def test_producto_sin_categoria_se_omite(self, db_conn, tmp_path):
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Official Sleeves 13", "code": None},  # OTROS -- sin categoría en D.2
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)
        assert result["inserted"] == []
        assert len(result["skipped"]) == 1

    def test_idempotente_no_duplica_en_segunda_ejecucion(self, db_conn, tmp_path):
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Starter Deck: Straw Hat Crew", "code": "ST-01"},
        ])
        soc.seed_from_catalog(db_conn, catalog_path)
        result2 = soc.seed_from_catalog(db_conn, catalog_path)

        assert result2["inserted"] == []
        assert len(result2["already_existed"]) == 2  # EN + JP (starter-deck, punto 4)
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM product")
            assert cur.fetchone()[0] == 2  # no se duplicó ninguna de las dos

    def test_idempotente_tambien_para_la_variante_jp(self, db_conn, tmp_path):
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Booster Pack: Romance Dawn", "code": "OP-01"},
        ])
        soc.seed_from_catalog(db_conn, catalog_path)
        result2 = soc.seed_from_catalog(db_conn, catalog_path)

        assert result2["inserted"] == []
        assert len(result2["already_existed"]) == 4
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM product")
            assert cur.fetchone()[0] == 4  # no se duplicó ninguna de las 4 variantes

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

    def test_booster_pack_se_siembra_como_caja_y_sobre(self, db_conn, tmp_path):
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Booster Pack: Romance Dawn", "code": "OP-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)

        assert len(result["inserted"]) == 2
        with db_conn.cursor() as cur:
            cur.execute("SELECT name_canonical FROM product ORDER BY name_canonical")
            names = [row[0] for row in cur.fetchall()]
        assert names == [
            "Booster Box: Romance Dawn OP-01 EN",
            "Booster Pack: Romance Dawn OP-01 EN",
        ]

    def test_starter_deck_se_siembra_una_sola_vez(self, db_conn, tmp_path):
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Starter Deck: Straw Hat Crew", "code": "ST-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)
        assert len(result["inserted"]) == 1

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
        assert len(result2["already_existed"]) == 1
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM product")
            assert cur.fetchone()[0] == 1  # no se duplicó

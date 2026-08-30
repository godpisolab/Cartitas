"""Tests de seed_official_catalog.py.

Taxonomía nueva (Recognition Pipeline): caja/sobre/case ya no son
categorías separadas (antes booster-box/booster-pack/booster-case) -- viven
en la MISMA categoría de família (one-piece/extra-booster/
premium-booster-box), distinguidos por `product.packaging`. Premium
Collection se separa en premium-card-collection (con variante JP desde
2026-08-30, demanda real confirmada) y premium-booster-box (PRB-01/02,
confirmados antes)."""

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


class TestToDisplayVariant:
    def test_starter_deck_con_dos_puntos_genera_display(self):
        assert soc._to_display_variant("Starter Deck: Straw Hat Crew") == "Starter Deck Display: Straw Hat Crew"

    def test_starter_deck_ex_genera_display(self):
        # Prefijo suelto ("Starter Deck", sin ":") a propósito -- el
        # catálogo oficial no es uniforme, "Starter Deck EX:" es real.
        assert soc._to_display_variant("Starter Deck EX: Luffy & Ace") == "Starter Deck Display EX: Luffy & Ace"

    def test_starter_deck_sin_dos_puntos_genera_display(self):
        # "Starter Deck ONE PIECE FILM edition" -- real, sin ":".
        assert soc._to_display_variant("Starter Deck ONE PIECE FILM edition") == \
            "Starter Deck Display ONE PIECE FILM edition"

    def test_double_pack_set_genera_display(self):
        assert soc._to_display_variant("Double Pack Set Vol.1") == "Double Pack Set Display Vol.1"

    def test_producto_sin_variante_display_conocida_devuelve_none(self):
        assert soc._to_display_variant("Booster Pack: Romance Dawn") is None
        assert soc._to_display_variant("Illustration Box Vol.1") is None


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
        for slug, name in [("one-piece", "One Piece"), ("starter-deck", "Starter Deck")]:
            cur.execute("INSERT INTO category (name, slug) VALUES (%s, %s)", (name, slug))
    conn.commit()


class TestSeedFromCatalog:
    def test_sin_game_one_piece_lanza_runtimeerror(self, db_conn, tmp_path):
        catalog_path = _write_catalog(tmp_path, [])
        import pytest
        with pytest.raises(RuntimeError, match="one-piece"):
            soc.seed_from_catalog(db_conn, catalog_path)

    def test_booster_pack_se_siembra_como_sobre_caja_y_case_misma_categoria_en_y_jp(self, db_conn, tmp_path):
        # one-piece está en _JP_VARIANT_CATEGORY_SLUGS -- cada release se
        # siembra 6 veces (sobre/caja/case, EN+JP cada uno), todas en la
        # MISMA categoría 'one-piece', distinguidas por packaging. A
        # diferencia del sistema anterior, el Case ya no se puede omitir
        # seleccionando solo sembrar la categoría 'booster-box'/'booster-pack'
        # sin 'booster-case' -- packaging='case' se genera siempre que la
        # família tenga multiplicador conocido en _PACKAGING_UNITS (aquí,
        # ONE_PIECE x12), independientemente de categorías.
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Booster Pack: Romance Dawn", "code": "OP-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)

        assert len(result["inserted"]) == 6
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT p.name_canonical, p.language, p.packaging, c.slug FROM product p "
                "JOIN category c ON c.id = p.category_id ORDER BY p.name_canonical"
            )
            rows = cur.fetchall()
        assert rows == [
            ("Booster Box Case: Romance Dawn (x12) OP-01 EN", "EN", "case", "one-piece"),
            ("Booster Box Case: Romance Dawn (x12) OP-01 JP", "JP", "case", "one-piece"),
            ("Booster Box: Romance Dawn OP-01 EN", "EN", "display", "one-piece"),
            ("Booster Box: Romance Dawn OP-01 JP", "JP", "display", "one-piece"),
            ("Booster Pack: Romance Dawn OP-01 EN", "EN", "sobre", "one-piece"),
            ("Booster Pack: Romance Dawn OP-01 JP", "JP", "sobre", "one-piece"),
        ]

    def test_premium_booster_genera_case_x10_en_premium_booster_box(self, db_conn, tmp_path):
        # Multiplicador DISTINTO para premium-booster-box (x10, verificado
        # contra el único PRB-NN real visto en el CSV: "(CASE) THE BEST 2
        # PRB-02 x10") -- no asume el mismo x12 que one-piece.
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece')")
            cur.execute("INSERT INTO category (name, slug) VALUES ('Premium Booster Box', 'premium-booster-box')")
        db_conn.commit()
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Premium Booster: One Piece Card The Best vol.2", "code": "PRB-02"},
        ])
        soc.seed_from_catalog(db_conn, catalog_path)

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT p.name_canonical, p.packaging, c.slug FROM product p "
                "JOIN category c ON c.id = p.category_id WHERE p.name_canonical LIKE %s",
                ("%Case%",),
            )
            rows = {name: (packaging, slug) for name, packaging, slug in cur.fetchall()}
        assert rows.get("Premium Booster Box Case: One Piece Card The Best vol.2 (x10) PRB-02 EN") == \
            ("case", "premium-booster-box")

    def test_starter_deck_no_genera_case_sin_evidencia_real(self, db_conn, tmp_path):
        # STARTER_DECK no tiene "case" en _PACKAGING_UNITS a propósito --
        # ninguna mención real de "Starter Deck Case" en el CSV, no se
        # inventa un multiplicador sin dato. SÍ genera variante 'display' Y
        # JP (ver test de abajo) -- comprueba que ningún "... Case" se cuela
        # entre los insertados.
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Starter Deck: Straw Hat Crew", "code": "ST-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)

        assert len(result["inserted"]) == 4
        assert not any("Case" in name for name in result["inserted"])

    def test_starter_deck_genera_variante_jp_y_display(self, db_conn, tmp_path):
        # starter-deck está en _JP_VARIANT_CATEGORY_SLUGS -- señal de
        # demanda real confirmada (Pokemillon vendía 7+ Starter Decks
        # japoneses sin ningún candidato JP posible). Variante 'display'
        # añadida 2026-08-30 -- 9 store_product reales (ej. "... [ST21] x6")
        # clasificaban packaging='display' sin ningún canónico con el que
        # confirmar (_PACKAGING_UNITS ya declaraba el multiplicador x6,
        # pero esta siembra nunca generaba esa fila).
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Starter Deck: Straw Hat Crew", "code": "ST-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)
        assert len(result["inserted"]) == 4
        assert set(result["inserted"]) == {
            "Starter Deck: Straw Hat Crew ST-01 EN", "Starter Deck: Straw Hat Crew ST-01 JP",
            "Starter Deck Display: Straw Hat Crew ST-01 EN", "Starter Deck Display: Straw Hat Crew ST-01 JP",
        }

    def test_double_pack_y_premium_card_collection_generan_ambos_jp(self, db_conn, tmp_path):
        # double-pack está en _JP_VARIANT_CATEGORY_SLUGS desde el principio
        # (demanda real confirmada, PRB-01/02); premium-card-collection se
        # añadió 2026-08-30 -- 13 store_product reales con raw_variant
        # "Japonés" encontrados en la cola de needs_review. Premium Card
        # Collection no tiene box-variant que duplicar (_to_box_variant solo
        # dobla "Booster/Extra Booster/Premium Booster:") -- 2 (Premium Card
        # Collection EN+JP) + 4 (Double Pack sobre+display, EN+JP cada uno,
        # ver _to_display_variant) = 6 insertados.
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO game (name, slug) VALUES ('One Piece', 'one-piece')")
            for slug, name in [
                ("premium-card-collection", "Premium Card Collection"), ("double-pack", "Double Pack"),
            ]:
                cur.execute("INSERT INTO category (name, slug) VALUES (%s, %s)", (name, slug))
        db_conn.commit()
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Premium Card Collection -25th Edition-", "code": None},
            {"name": "Double Pack Set Vol.1", "code": "DP-01"},
        ])
        result = soc.seed_from_catalog(db_conn, catalog_path)

        assert len(result["inserted"]) == 6
        with db_conn.cursor() as cur:
            cur.execute("SELECT name_canonical, language FROM product ORDER BY name_canonical")
            rows = cur.fetchall()
        assert rows == [
            ("Double Pack Set Display Vol.1 DP-01 EN", "EN"),
            ("Double Pack Set Display Vol.1 DP-01 JP", "JP"),
            ("Double Pack Set Vol.1 DP-01 EN", "EN"),
            ("Double Pack Set Vol.1 DP-01 JP", "JP"),
            ("Premium Card Collection -25th Edition- EN", "EN"),
            ("Premium Card Collection -25th Edition- JP", "JP"),
        ]

    def test_producto_sin_categoria_se_omite(self, db_conn, tmp_path):
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Official Storage Box", "code": None},  # OTROS -- accesorio sin família reconocida
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
        # 4, no 1 -- starter-deck genera sobre+display, cada uno EN + JP.
        assert len(result2["already_existed"]) == 4
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM product")
            assert cur.fetchone()[0] == 4  # no se duplicó ninguna de las cuatro

    def test_idempotente_tambien_para_la_variante_jp(self, db_conn, tmp_path):
        _seed_game_and_categories(db_conn)
        catalog_path = _write_catalog(tmp_path, [
            {"name": "Booster Pack: Romance Dawn", "code": "OP-01"},
        ])
        soc.seed_from_catalog(db_conn, catalog_path)
        result2 = soc.seed_from_catalog(db_conn, catalog_path)

        assert result2["inserted"] == []
        assert len(result2["already_existed"]) == 6
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM product")
            assert cur.fetchone()[0] == 6  # no se duplicó ninguna de las 6 variantes (sobre/caja/case x EN/JP)

    def test_autocorrige_set_code_desfasado_de_una_fila_ya_sembrada(self, db_conn, tmp_path):
        """Bug real (2026-08-30): "Official Sleeves 16" se sembró con
        set_code NULL porque en su momento classify_product() no sabía
        reconocer esta família -- una vez arreglado classify.py, relanzar el
        seeder debe actualizar la fila existente en vez de dejarla desfasada
        para siempre (idempotencia por name_canonical la protegía de
        duplicarse, pero nunca la corregía)."""
        _seed_game_and_categories(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO category (name, slug) VALUES ('Sleeves', 'sleeves')")
            cur.execute("SELECT id FROM game WHERE slug = 'one-piece'")
            game_id = cur.fetchone()[0]
            cur.execute("SELECT id FROM category WHERE slug = 'sleeves'")
            category_id = cur.fetchone()[0]
            # Simula el estado "de antes del fix": insertado a mano con
            # set_code NULL, tal como habría quedado la siembra vieja.
            cur.execute(
                "INSERT INTO product (game_id, category_id, set_code, language, name_canonical) "
                "VALUES (%s, %s, NULL, 'EN', 'Official Sleeves 16 EN')",
                (game_id, category_id),
            )
        db_conn.commit()

        catalog_path = _write_catalog(tmp_path, [{"name": "Official Sleeves 16", "code": None}])
        result = soc.seed_from_catalog(db_conn, catalog_path)

        assert result["inserted"] == []
        assert result["updated"] == ["Official Sleeves 16 EN"]
        with db_conn.cursor() as cur:
            cur.execute("SELECT set_code FROM product WHERE name_canonical = 'Official Sleeves 16 EN'")
            assert cur.fetchone()[0] == "VOL16"
            cur.execute("SELECT count(*) FROM product")
            assert cur.fetchone()[0] == 1  # se actualizó la fila, no se insertó una segunda

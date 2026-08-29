"""Siembra el catálogo canónico `product` desde el catálogo oficial de
Bandai (data/one_piece_tcg_products.json, en.onepiece-cardgame.com, versión
EN, hasta 2026-08-26).

Reutiliza classify_product() -- LA MISMA función que ya usa el scraper y el
matcher -- para derivar category/main_set/set_code/language de cada nombre,
en vez de escribir una heurística de siembra paralela. Así el canónico y lo
que el matcher deriva de raw_name en vivo hablan el mismo idioma por
construcción (C.4: no es aprendizaje automático, son las mismas reglas).

Decisión de modelado -- Booster Box y Booster Pack por separado: el
catálogo oficial lista una única fila por lanzamiento ("Booster Pack: X"),
pero las tiendas reales venden la caja completa y el sobre suelto como SKUs
distintos con precios muy distintos (verificado en Arte9: ~110-199€ la caja
vs ~5€ el sobre del mismo set) -- el diseño funcional ya los trata como
tipos de producto filtrables por separado. Por eso cada lanzamiento tipo
booster se siembra DOS veces (Booster Box + Booster Pack), no una.

Qué se OMITE a propósito: productos sin categoría en nuestra taxonomía de
13 tipos (D.2) -- fundas, binders, cajas de almacenaje sueltas, sets de
aniversario, "Special Goods Set", "Tin Pack Set", "DON!! Set", "Sound
Loader"... classify_product() los deja en OTROS (sin keyword que los
reconozca) y este script los excluye de la siembra, igual que el matcher
los excluye del pipeline en vivo (C.5/D.3: no existe un canónico razonable
sin categoría). Se listan al final de la ejecución para decidir si conviene
ampliar D.2 más adelante -- no se inventa una categoría nueva sin que
alguien lo decida.

Idempotente: si ya existe un `product` con el mismo name_canonical para
one-piece, se omite en vez de duplicar -- se puede re-ejecutar sin miedo
cuando se actualice el JSON con productos nuevos.
"""

from __future__ import annotations

import json
from pathlib import Path

from shared.classify import classify_product
from matcher import NOT_APPLICABLE_PRODUCT_TYPES, PRODUCT_TYPE_TO_CATEGORY_SLUG
from persistence import get_connection

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "one_piece_tcg_products.json"

# Prefijos de "release por sobres" del catálogo oficial -- se siembran como
# Booster Box Y Booster Pack (ver docstring del módulo). Cualquier producto
# cuyo nombre no empiece por uno de estos prefijos se siembra tal cual, una
# sola vez (no aplica a Starter Deck, Illustration Box, etc.).
_BOOSTER_LABEL_SWAPS = [
    ("Booster Pack:", "Booster Box:"),
    ("Extra Booster:", "Extra Booster Box:"),
    ("Premium Booster:", "Premium Booster Box:"),
]

# Categorías con import japonés real y volumen significativo (2026-08-27,
# investigación sobre multi_tienda_one_piece.csv real: 164/1536 filas --
# 10.7% -- en JP, mayoritariamente boosters/cajas). El catálogo oficial de
# Bandai (data/one_piece_tcg_products.json) es SOLO la versión EN --
# product.language valía 'EN' para el 100% de las filas sembradas hasta
# ahora, así que un store_product JP jamás podía llegar a `confirmed`
# (language_matches siempre False, sin ningún candidato JP con el que
# coincidir en absoluto -- no es que saliera en 2º lugar, no existía).
# Ampliado (2026-08-28, docs/pendientes-motor-matching.md punto 6 --
# decidido): a todo lo que sea producto sellado con importación japonesa
# real demostrada en el CSV, no solo Booster Box/Pack -- 16 filas
# PRB-01/PRB-02 en japonés que hoy no pueden confirmar por falta de esa
# variante. booster-case incluida a propósito (una vez sembrados sus
# canónicos, ver seed_from_catalog más abajo) -- un Case es, en esencia,
# varias Booster Box del mismo lanzamiento, mismo criterio de demanda JP.
# starter-deck añadida (2026-08-28/29, docs/propuesta-mejoras-matching-sesion.md
# punto 4 y docs/pendientes-motor-matching.md punto 6 -- decidido en dos
# sesiones de auditoría independientes con la misma evidencia): 7 ejemplos
# reales de demanda JP encontrados revisando la cola (ST-08/ST-09/ST-11/
# ST-14/ST-33/ST-34/ST-36, todos de Pokemillon) -- la señal de volumen que
# se dejaba como condición para ampliar la lista ya está aquí.
# illustration-box/devil-fruits-collection/learn-deck siguen fuera A
# PROPÓSITO, sin confirmación de demanda real todavía -- no es descarte, es
# "sin decidir" (generar de más no rompe nada, pero tampoco hay señal que
# lo pida hoy).
_JP_VARIANT_CATEGORY_SLUGS = {
    "booster-box", "booster-pack", "booster-case", "double-pack", "premium-collection", "starter-deck",
}


def _build_name(name: str, code: str | None, language: str = "EN") -> str:
    """Construye un texto 'al estilo raw_name de tienda' -- nombre + código
    + idioma -- para que classify_product() derive category/main_set/
    set_code exactamente igual que lo haría de un raw_name real scrapeado."""
    parts = [name]
    if code:
        # Códigos partidos como "OP-14 / EB-04 (parte 1)" -- solo el primer
        # código real importa para los regex de main_set/set_code.
        parts.append(code.split("/")[0].strip())
    parts.append(language)
    return " ".join(parts)


def _to_box_variant(name: str) -> str | None:
    """None si `name` no es un release tipo booster (no se duplica)."""
    for pack_label, box_label in _BOOSTER_LABEL_SWAPS:
        if name.startswith(pack_label):
            return name.replace(pack_label, box_label, 1)
    return None


# Multiplicador de cajas por Case (2026-08-28, docs/pendientes-motor-matching.md
# punto 3) -- NO es uniforme entre líneas de producto, verificado contra las
# 34 menciones reales de "case" en multi_tienda_one_piece.csv: los OP-NN/
# EB-NN vistos (booster-box, vía "Booster Pack:"/"Extra Booster:") son
# siempre x12; el único PRB-NN visto (premium-collection, vía "Premium
# Booster:", "(CASE) THE BEST 2 – PRB-02 – x10") es x10. Solo se siembra
# Case para categorías con evidencia real -- Starter Deck/Double Pack no
# tienen NINGUNA mención de "Case" en esas 34 filas, no se inventa un
# número para ellos sin dato.
_CASE_MULTIPLIER_BY_CATEGORY = {
    "booster-box": 12,
    "premium-collection": 10,
}

_BOOSTER_CASE_LABEL_SWAPS = [
    ("Booster Pack:", "Booster Box Case:"),
    ("Extra Booster:", "Extra Booster Box Case:"),
    ("Premium Booster:", "Premium Booster Box Case:"),
]


def _to_case_variant(name: str, multiplier: int) -> str | None:
    """None si `name` no es un release tipo booster (no tiene Case). El
    multiplicador va en el propio nombre canónico (ej. "(x12)") -- ayuda a
    la similitud de texto contra raw_names reales, que casi siempre lo
    mencionan ("Booster Box Case (12 Boxes)", "CASE... x10 Booster Box")."""
    for pack_label, case_label in _BOOSTER_CASE_LABEL_SWAPS:
        if name.startswith(pack_label):
            return f"{name.replace(pack_label, case_label, 1)} (x{multiplier})"
    return None


def seed_from_catalog(conn, catalog_path: Path = CATALOG_PATH) -> dict:
    with open(catalog_path, encoding="utf-8") as f:
        catalog = json.load(f)

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM game WHERE slug = 'one-piece'")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("Falta el game 'one-piece' -- aplica seed-catalog-app-tcg.sql primero (D.2)")
        game_id = row[0]

        cur.execute("SELECT slug, id FROM category")
        category_ids = dict(cur.fetchall())

    inserted: list[str] = []
    skipped: list[str] = []
    already_existed: list[str] = []

    def _insert_if_new(cur, name: str, code: str | None, language: str) -> str | None:
        """Clasifica+inserta una variante de idioma concreta -- devuelve el
        category_slug si se insertó/ya existía (para que el llamador sepa
        si vale la pena generar también la variante JP), None si se omitió
        (sin categoría en D.2)."""
        built_name = _build_name(name, code, language)
        classification = classify_product(built_name)

        if classification.product_type in NOT_APPLICABLE_PRODUCT_TYPES:
            skipped.append(built_name)
            return None

        category_slug = PRODUCT_TYPE_TO_CATEGORY_SLUG.get(classification.product_type)
        category_id = category_ids.get(category_slug) if category_slug else None
        if category_id is None:
            skipped.append(built_name)
            return None

        cur.execute(
            "SELECT 1 FROM product WHERE game_id = %s AND name_canonical = %s",
            (game_id, built_name),
        )
        if cur.fetchone():
            already_existed.append(built_name)
            return category_slug

        cur.execute(
            """
            INSERT INTO product (game_id, category_id, set_code, main_set, language, name_canonical)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (game_id, category_id, classification.set_code, classification.main_set,
             classification.language, built_name),
        )
        inserted.append(built_name)
        return category_slug

    with conn.cursor() as cur:
        for item in catalog["products"]:
            box_name = _to_box_variant(item["name"])
            name_code_pairs = [(item["name"], item["code"])]
            if box_name:
                name_code_pairs.append((box_name, item["code"]))

            box_category_slug = None
            for name, code in name_code_pairs:
                category_slug = _insert_if_new(cur, name, code, "EN")
                if name == box_name:
                    box_category_slug = category_slug
                # Variante JP (ver _JP_VARIANT_CATEGORY_SLUGS) -- solo si la
                # versión EN sí tiene categoría reconocida, mismo criterio
                # que ya usa el resto del script para "vale la pena
                # sembrarlo".
                if category_slug in _JP_VARIANT_CATEGORY_SLUGS:
                    _insert_if_new(cur, name, code, "JP")

            # Case (punto 3) -- solo para releases booster con multiplicador
            # conocido (ver _CASE_MULTIPLIER_BY_CATEGORY). Se deriva del
            # nombre EN suelto (no del box_name ya transformado), para que
            # _to_case_variant reconozca el prefijo original ("Booster
            # Pack:"/"Extra Booster:"/"Premium Booster:").
            multiplier = _CASE_MULTIPLIER_BY_CATEGORY.get(box_category_slug)
            if multiplier is not None:
                case_name = _to_case_variant(item["name"], multiplier)
                if case_name:
                    case_category_slug = _insert_if_new(cur, case_name, item["code"], "EN")
                    if case_category_slug in _JP_VARIANT_CATEGORY_SLUGS:
                        _insert_if_new(cur, case_name, item["code"], "JP")

    conn.commit()
    return {
        "inserted": inserted,
        "already_existed": already_existed,
        "skipped": sorted(set(skipped)),
    }


if __name__ == "__main__":
    conn = get_connection()
    try:
        result = seed_from_catalog(conn)
    finally:
        conn.close()

    print(f"Insertados: {len(result['inserted'])}")
    print(f"Ya existían (idempotencia): {len(result['already_existed'])}")
    print(f"\nOmitidos ({len(result['skipped'])}, sin categoría en D.2 -- classify_product() los marca OTROS/LOTE_CARTAS):")
    for name in result["skipped"]:
        print(f"  - {name}")

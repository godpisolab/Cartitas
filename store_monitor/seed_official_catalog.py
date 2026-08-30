"""Siembra el catálogo canónico `product` desde el catálogo oficial de
Bandai (data/one_piece_tcg_products.json, en.onepiece-cardgame.com, versión
EN, hasta 2026-08-26).

Reutiliza classify_product() -- LA MISMA función que ya usa el scraper y el
matcher -- para derivar category/main_set/set_code/language/packaging de
cada nombre, en vez de escribir una heurística de siembra paralela. Así el
canónico y lo que el matcher deriva de raw_name en vivo hablan el mismo
idioma por construcción (C.4: no es aprendizaje automático, son las mismas
reglas).

Decisión de modelado -- una fila por variante de empaquetado, packaging
distinto por fila: el catálogo oficial lista una única fila por lanzamiento
("Booster Pack: X"), pero las tiendas reales venden el sobre suelto, la caja
completa y el case como SKUs distintos con precios muy distintos (verificado
en Arte9: ~110-199€ la caja vs ~5€ el sobre del mismo set) -- el diseño
funcional ya los trata como variantes filtrables por separado. Por eso cada
lanzamiento tipo booster se siembra hasta TRES veces (sobre + caja + case),
todas en la MISMA categoría (Recognition Pipeline,
docs/propuestas/guia_nuevo_matcher.md -- antes eran tres categorías
distintas: Booster Box/Booster Pack/Booster Case), distinguidas por
`product.packaging`.

Qué se OMITE a propósito: productos sin categoría en la taxonomía de 10
tipos matcheables -- fundas sin línea reconocida, binders, cajas de
almacenaje sueltas, sets de aniversario, "Special Goods Set", "Tin Pack
Set", "DON!! Set", "Sound Loader"... classify_product() los deja en OTROS
(sin keyword que los reconozca) y este script los excluye de la siembra,
igual que el matcher los excluye del pipeline en vivo (no existe un
canónico razonable sin categoría). Se listan al final de la ejecución para
decidir si conviene ampliar la taxonomía más adelante -- no se inventa una
categoría nueva sin que alguien lo decida.

Idempotente Y autocorrectivo: si ya existe un `product` con el mismo
name_canonical para one-piece, no se duplica -- pero si set_code/main_set/
language/packaging derivados ahora por classify_product() difieren de lo
que quedó guardado, se ACTUALIZAN en la fila existente. Sin esto, una fila
sembrada antes de un cambio de classify.py (p.ej. una família nueva que
antes no sabía extraer código, como pasó con las 16 versiones de "Official
Sleeves N") se queda con el valor viejo (a menudo NULL) para siempre, y
volver a correr este script no la arregla -- se puede re-ejecutar sin miedo
tanto para productos nuevos del JSON como para recalcular los ya sembrados.
"""

from __future__ import annotations

import json
from pathlib import Path

from shared.classify import _PACKAGING_UNITS, classify_product
from matcher import NOT_APPLICABLE_PRODUCT_TYPES, PRODUCT_TYPE_TO_CATEGORY_SLUG
from persistence import get_connection

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "one_piece_tcg_products.json"

# Prefijos de "release por sobres" del catálogo oficial -- se siembran como
# sobre Y caja (ver docstring del módulo). Starter Deck/Double Pack tienen
# su PROPIO mecanismo de variante 'display' (_to_display_variant, más abajo
# -- swap de prefijo suelto, no de label exacto); Illustration Box/Devil
# Fruits Collection/etc. siguen sin variante de caja, sin evidencia real
# todavía (ver _PACKAGING_UNITS).
_BOOSTER_LABEL_SWAPS = [
    ("Booster Pack:", "Booster Box:"),
    ("Extra Booster:", "Extra Booster Box:"),
    ("Premium Booster:", "Premium Booster Box:"),
]

_BOOSTER_CASE_LABEL_SWAPS = [
    ("Booster Pack:", "Booster Box Case:"),
    ("Extra Booster:", "Extra Booster Box Case:"),
    ("Premium Booster:", "Premium Booster Box Case:"),
]

# Categorías con import japonés real y volumen significativo (2026-08-27/28,
# investigación sobre multi_tienda_one_piece.csv real: boosters/cajas y
# Premium Booster Box PRB-01/02 con demanda JP demostrada, más Starter Deck
# -- 7 ejemplos reales encontrados en dos auditorías de cola independientes;
# premium-card-collection añadida 2026-08-30 -- 13 store_product reales con
# raw_variant "Japonés" en la cola de needs_review, ver export_needs_review_csv.py).
# El catálogo oficial de Bandai (data/one_piece_tcg_products.json) es SOLO
# la versión EN -- sin esta lista, product.language valdría 'EN' para el
# 100% de las filas sembradas, y un store_product JP jamás podría llegar a
# 'confirmed' (ningún candidato JP con el que coincidir en absoluto).
# illustration-box/devil-fruits-collection/sleeves siguen fuera A PROPÓSITO,
# sin confirmación de demanda real todavía -- no es descarte, es "sin decidir".
_JP_VARIANT_CATEGORY_SLUGS = {
    "one-piece", "extra-booster", "premium-booster-box", "double-pack", "starter-deck",
    "premium-card-collection",
}


def _build_name(name: str, code: str | None, language: str = "EN") -> str:
    """Construye un texto 'al estilo raw_name de tienda' -- nombre + código
    + idioma -- para que classify_product() derive category/main_set/
    set_code/packaging exactamente igual que lo haría de un raw_name real
    scrapeado."""
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


# Starter Deck/Double Pack -- prefijo suelto ("Starter Deck", sin exigir
# ":" porque el catálogo oficial no es uniforme: "Starter Deck: X", "Starter
# Deck EX: X" y "Starter Deck ONE PIECE FILM edition" sin dos puntos,
# los tres reales) en vez del swap de label exacto que usan los boosters.
_DISPLAY_LABEL_SWAPS = [
    ("Starter Deck", "Starter Deck Display"),
    ("Double Pack Set", "Double Pack Set Display"),
]


def _to_display_variant(name: str) -> str | None:
    """None si `name` no es de una família con variante 'display' conocida
    (ver _PACKAGING_UNITS) -- bug real corregido 2026-08-30: Starter Deck
    (x6) y Double Pack (x10) SÍ tienen multiplicador de display en
    _PACKAGING_UNITS (así que classify_product() ya sabía detectar
    packaging='display' en un raw_name real, ej. "... [ST21] x6"), pero esta
    siembra nunca generaba el canónico correspondiente -- ese packaging
    nunca podía tener un candidato exacto con el que confirmar (12 filas
    reales encontradas en needs_review.csv: 9 Starter Deck + 3 Double
    Pack)."""
    for label, display_label in _DISPLAY_LABEL_SWAPS:
        if name.startswith(label):
            return name.replace(label, display_label, 1)
    return None


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
    updated: list[str] = []
    skipped: list[str] = []
    already_existed: list[str] = []

    def _insert_if_new(cur, name: str, code: str | None, language: str) -> str | None:
        """Clasifica+inserta (o actualiza si ya existía y quedó desfasada)
        una variante de idioma concreta -- devuelve el category_slug si se
        insertó/actualizó/ya coincidía (para que el llamador sepa si vale la
        pena generar también la variante JP), None si se omitió (sin
        categoría reconocida)."""
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

        derived = (classification.set_code, classification.main_set, classification.language, classification.packaging)

        cur.execute(
            "SELECT id, set_code, main_set, language, packaging FROM product WHERE game_id = %s AND name_canonical = %s",
            (game_id, built_name),
        )
        existing = cur.fetchone()
        if existing:
            existing_id, *existing_derived = existing
            if tuple(existing_derived) != derived:
                cur.execute(
                    "UPDATE product SET set_code = %s, main_set = %s, language = %s, packaging = %s WHERE id = %s",
                    (*derived, existing_id),
                )
                updated.append(built_name)
            else:
                already_existed.append(built_name)
            return category_slug

        cur.execute(
            """
            INSERT INTO product (game_id, category_id, set_code, main_set, language, packaging, name_canonical)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (game_id, category_id, *derived, built_name),
        )
        inserted.append(built_name)
        return category_slug

    with conn.cursor() as cur:
        for item in catalog["products"]:
            # Clasificación de referencia SOLO para saber a qué família
            # pertenece este release (y así qué multiplicador de Case
            # usar, si aplica) -- cada variante real (sobre/caja/case) se
            # clasifica DE NUEVO más abajo con su propio texto, porque
            # packaging depende del texto de CADA variante, no es el mismo
            # para las tres.
            base_product_type = classify_product(_build_name(item["name"], item["code"], "EN")).product_type

            box_name = _to_box_variant(item["name"])
            display_name = _to_display_variant(item["name"])
            name_code_pairs = [(item["name"], item["code"])]
            if box_name:
                name_code_pairs.append((box_name, item["code"]))
            if display_name:
                name_code_pairs.append((display_name, item["code"]))

            for name, code in name_code_pairs:
                category_slug = _insert_if_new(cur, name, code, "EN")
                # Variante JP -- solo si la versión EN sí tiene categoría
                # reconocida, mismo criterio que ya usa el resto del script
                # para "vale la pena sembrarlo".
                if category_slug in _JP_VARIANT_CATEGORY_SLUGS:
                    _insert_if_new(cur, name, code, "JP")

            # Case -- solo para família con multiplicador conocido (ver
            # _PACKAGING_UNITS en shared.classify, misma fuente que usa
            # _detect_packaging en vivo -- ya no hay una copia duplicada
            # de este dato aquí).
            case_units = _PACKAGING_UNITS.get(base_product_type, {}).get("case")
            if case_units is not None:
                case_name = _to_case_variant(item["name"], case_units)
                if case_name:
                    case_category_slug = _insert_if_new(cur, case_name, item["code"], "EN")
                    if case_category_slug in _JP_VARIANT_CATEGORY_SLUGS:
                        _insert_if_new(cur, case_name, item["code"], "JP")

    conn.commit()
    return {
        "inserted": inserted,
        "updated": updated,
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
    print(f"Actualizados (set_code/main_set/language/packaging desfasados): {len(result['updated'])}")
    for name in result["updated"]:
        print(f"  - {name}")
    print(f"Ya existían sin cambios: {len(result['already_existed'])}")
    print(f"\nOmitidos ({len(result['skipped'])}, sin categoría reconocida -- classify_product() los marca OTROS/not_applicable):")
    for name in result["skipped"]:
        print(f"  - {name}")

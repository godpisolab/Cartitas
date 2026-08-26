"""Motor de matching: store_product.raw_name (ya en BBDD) -> product
canónico. Bloque C de cambios-necesarios-scraper.md.

No es aprendizaje automático (C.4): classify_product() es determinista
(reglas fijas de texto, las mismas que ya usa el scraper) y pg_trgm es
similitud de texto sobre name_canonical, no un modelo que mejora con el uso.
Si el panel de revisión revela un patrón de error recurrente, la corrección
es editar CLASSIFICATION_RULES a mano, no reentrenar nada.

Umbrales (C.2, aprobados 2026-08-26 -- valores de partida, a calibrar con
datos reales):
- main_set + product_type + language exactos, y similarity > 0.6
  -> 'confirmed' automático.
- main_set + product_type coinciden, pero similarity en [0.35, 0.6)
  (o el idioma no coincide/no se detectó) -> 'needs_review'.
- main_set NO coincide (aunque el product_type sí -- probablemente el mismo
  tipo de producto pero de OTRO lanzamiento, similitud de texto engañosa),
  o similarity < 0.35 -> 'unmatched'.

LOTE_CARTAS y OTROS (C.5/D.3) nunca entran en este pipeline -- se marcan
'not_applicable' directamente, sin gastar una consulta de similitud: un
lote es por definición una combinación única de cartas, no existe un
canónico razonable con el que compararlo.

El top-3 de candidatos (C.3) NO se guarda aquí -- se calcula en caliente en
el futuro endpoint GET /matches/pending vía `ORDER BY
similarity(name_canonical, raw_name) DESC LIMIT 3` sobre
idx_product_name_trgm (ver comentario en schema-postgresql-app-tcg.sql).
Este módulo solo decide y persiste el match_status y, si corresponde,
product_id + match_confidence del MEJOR candidato.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from base_script import Classification, classify_product

CONFIRMED_SIMILARITY_THRESHOLD = 0.6
REVIEW_SIMILARITY_THRESHOLD = 0.35

# Mapea Classification.product_type (base_script.CLASSIFICATION_RULES) a
# category.slug (D.2 -- los 13 tipos reales, sembrados por
# seed-catalog-app-tcg.sql). LOTE_CARTAS y OTROS quedan fuera a propósito,
# ver NOT_APPLICABLE_PRODUCT_TYPES.
PRODUCT_TYPE_TO_CATEGORY_SLUG = {
    "BOOSTER_BOX": "booster-box",
    "BOOSTER_PACK": "booster-pack",
    "STARTER_DECK": "starter-deck",
    "ILLUSTRATION_BOX": "illustration-box",
    "PREMIUM_COLLECTION": "premium-collection",
    "DOUBLE_PACK": "double-pack",
    "MYSTERY_PACK": "mystery-pack",
    "DEVIL_FRUITS_COLLECTION": "devil-fruits-collection",
    "LEARN_DECK": "learn-deck",
    "PROMO_CARD": "promo-card",
    "PLAYMAT": "playmat",
    "DICE_ACCESSORY": "dice-accessory",
}

NOT_APPLICABLE_PRODUCT_TYPES = {"LOTE_CARTAS", "OTROS"}


@dataclass
class MatchOutcome:
    match_status: str
    product_id: Optional[int]
    match_confidence: Optional[float]


def _category_ids(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT slug, id FROM category")
        return dict(cur.fetchall())


def _best_candidate(cur, category_id: int, raw_name: str):
    """Top-1 candidato por similarity dentro de la categoría -- basta con 1
    para decidir el match_status; el top-3 completo (C.3) es responsabilidad
    del futuro endpoint de revisión, no de este matcher."""
    cur.execute(
        """
        SELECT id, main_set, language, similarity(name_canonical, %s) AS score
        FROM product
        WHERE category_id = %s
        ORDER BY score DESC
        LIMIT 1
        """,
        (raw_name, category_id),
    )
    return cur.fetchone()


def _evaluate(cur, category_ids: dict[str, int], classification: Classification, raw_name: str) -> MatchOutcome:
    if classification.product_type in NOT_APPLICABLE_PRODUCT_TYPES:
        return MatchOutcome("not_applicable", None, None)

    category_slug = PRODUCT_TYPE_TO_CATEGORY_SLUG.get(classification.product_type)
    category_id = category_ids.get(category_slug) if category_slug else None
    if category_id is None:
        # product_type reconocido por classify_product() pero sin categoría
        # sembrada todavía -- seed-catalog-app-tcg.sql desactualizado
        # respecto a CLASSIFICATION_RULES. No hay nada contra qué comparar.
        return MatchOutcome("unmatched", None, None)

    candidate = _best_candidate(cur, category_id, raw_name)
    if not candidate:
        return MatchOutcome("unmatched", None, None)

    product_id, main_set, language, score = candidate
    if score < REVIEW_SIMILARITY_THRESHOLD:
        return MatchOutcome("unmatched", None, None)

    if main_set != classification.main_set:
        # Mismo tipo de producto pero de un LANZAMIENTO distinto -- la
        # similitud de texto puede ser alta igualmente (ej. "Booster Box"
        # domina el nombre), pero no es el mismo producto. Nunca
        # needs_review ni confirmed en este caso.
        return MatchOutcome("unmatched", None, None)

    language_matches = classification.language is not None and language == classification.language
    if language_matches and score > CONFIRMED_SIMILARITY_THRESHOLD:
        return MatchOutcome("confirmed", product_id, score)

    return MatchOutcome("needs_review", None, None)


def run_matching(conn) -> dict[str, int]:
    """Reevalúa todos los store_product que no estén ya 'confirmed' (una
    confirmación es una decisión ya tomada -- C.4, esto no es ML, no se
    revierte sola) y actualiza su match_status/product_id/match_confidence.

    Deliberadamente reevalúa también los que ya están 'not_applicable' o
    'needs_review': si CLASSIFICATION_RULES cambia a mano (C.4) o se siembra
    un producto canónico nuevo, la siguiente pasada debe poder recalcular
    sin intervención manual adicional."""
    category_ids = _category_ids(conn)
    if not category_ids:
        raise RuntimeError(
            "La tabla category está vacía -- aplica seed-catalog-app-tcg.sql "
            "antes de correr el matcher (ver D.2)."
        )

    counts: dict[str, int] = defaultdict(int)

    with conn.cursor() as cur:
        cur.execute("SELECT id, raw_name, raw_variant FROM store_product WHERE match_status != 'confirmed'")
        rows = cur.fetchall()

        for store_product_id, raw_name, raw_variant in rows:
            classification = classify_product(raw_name, raw_variant)
            outcome = _evaluate(cur, category_ids, classification, raw_name)
            cur.execute(
                """
                UPDATE store_product
                SET match_status = %s, product_id = %s, match_confidence = %s
                WHERE id = %s
                """,
                (outcome.match_status, outcome.product_id, outcome.match_confidence, store_product_id),
            )
            counts[outcome.match_status] += 1

    conn.commit()
    return dict(counts)


def find_missing_canonical_candidates(conn, min_stores: int = 2) -> list[dict]:
    """C.1: agrupa los store_product SIN match confirmado por
    (product_type, main_set, language) derivados de su raw_name. Si varias
    tiendas DISTINTAS venden algo que parece el mismo producto y no hay
    ningún `product` candidato en esa categoría+main_set, es una señal de
    que falta sembrar un canónico -- pensado para que el futuro panel de
    revisión lo muestre como sugerencia de alta ("6 tiendas venden algo que
    parece OP17 Booster Box EN y no existe en el catálogo -- ¿lo creamos?").

    No crea nada automáticamente -- solo reporta. Ignora LOTE_CARTAS/OTROS
    (nunca tienen canónico) y agrupaciones con menos de `min_stores` tiendas
    distintas (ruido de una sola tienda, no una señal fuerte)."""
    category_ids = _category_ids(conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT store_id, raw_name, raw_variant FROM store_product WHERE match_status != 'confirmed'"
        )
        rows = cur.fetchall()

        groups: dict[tuple, set] = defaultdict(set)
        for store_id, raw_name, raw_variant in rows:
            classification = classify_product(raw_name, raw_variant)
            if classification.product_type in NOT_APPLICABLE_PRODUCT_TYPES:
                continue
            key = (classification.product_type, classification.main_set, classification.language)
            groups[key].add(store_id)

        suggestions = []
        for (product_type, main_set, language), store_ids in groups.items():
            if len(store_ids) < min_stores:
                continue

            category_slug = PRODUCT_TYPE_TO_CATEGORY_SLUG.get(product_type)
            category_id = category_ids.get(category_slug) if category_slug else None
            has_candidate = False
            if category_id is not None:
                cur.execute(
                    "SELECT 1 FROM product WHERE category_id = %s AND main_set IS NOT DISTINCT FROM %s LIMIT 1",
                    (category_id, main_set),
                )
                has_candidate = cur.fetchone() is not None

            if not has_candidate:
                suggestions.append({
                    "product_type": product_type,
                    "main_set": main_set,
                    "language": language,
                    "store_count": len(store_ids),
                })

    return sorted(suggestions, key=lambda s: s["store_count"], reverse=True)

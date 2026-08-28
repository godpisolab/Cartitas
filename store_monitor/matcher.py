"""Motor de matching: store_product.raw_name (ya en BBDD) -> product
canónico. Bloque C de cambios-necesarios-scraper.md.

No es aprendizaje automático (C.4): classify_product() es determinista
(reglas fijas de texto, las mismas que ya usa el scraper) y pg_trgm es
similitud de texto sobre name_canonical, no un modelo que mejora con el uso.
Si el panel de revisión revela un patrón de error recurrente, la corrección
es editar CLASSIFICATION_RULES a mano, no reentrenar nada.

Umbrales (C.2, aprobados 2026-08-26 -- valores de partida, a calibrar con
datos reales) y las cinco condiciones del auto-confirmado por set_code
(implementacion-auto-confirmado-setcode.md, 2026-08-27):
- Camino rápido: candidato PRIMARIO (no fallback cross-categoría, ver
  _best_candidate) con set_code + product_type + language exactos y
  cantidad no ambigua (ver shared.classify.cantidad_es_ambigua) ->
  'confirmed' automático, SIN depender de la similitud de texto -- un
  set_code exacto dentro de su propia categoría es señal suficiente.
- Camino de siempre (sin las cinco condiciones arriba, típicamente sin
  set_code detectable en algún lado): set_code + product_type coinciden y
  similarity > 0.6 con idioma exacto -> 'confirmed'; similarity en
  [0.35, 0.6) (o idioma no coincide/no detectado) -> 'needs_review'.
- set_code NO coincide (aunque el product_type sí -- probablemente el mismo
  tipo de producto pero de OTRO lanzamiento, similitud de texto engañosa),
  o (sin set_code exacto) similarity < 0.35 -> 'unmatched'.

(set_code en vez de main_set desde el 2026-08-27, revisión de la cola de
matching -- main_set solo está poblado para la familia OP por diseño; ver
_evaluate() para el detalle.)

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

from shared.classify import (
    NOT_APPLICABLE_PRODUCT_TYPES,
    PRODUCT_TYPE_TO_CATEGORY_SLUG,
    cantidad_es_ambigua,
    classify_product,
    classify_with_category,
    is_box_variant,
)
from shared.domain import Classification

CONFIRMED_SIMILARITY_THRESHOLD = 0.6
REVIEW_SIMILARITY_THRESHOLD = 0.35


@dataclass
class MatchOutcome:
    match_status: str
    product_id: Optional[int]
    match_confidence: Optional[float]


def _category_ids(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT slug, id FROM category")
        return dict(cur.fetchall())


def _best_candidate(cur, category_id: int, raw_name: str, set_code: Optional[str] = None,
                     language: Optional[str] = None):
    """Top-1 candidato dentro de la categoría -- basta con 1 para decidir el
    match_status; el top-3 completo (C.3) es responsabilidad del futuro
    endpoint de revisión, no de este matcher.

    Prioriza set_code EXACTO sobre similitud de texto pura (revisión manual
    de la cola de matching, 2026-08-27): varios raw_names de la misma
    familia comparten casi todo el texto salvo el código ("Starter Deck:
    Red Monkey.D.Luffy ST-31" vs "...ST-30"), así que similarity() por sí
    sola puede preferir un candidato genérico/de otro código sobre el
    correcto. Cuando el raw_name trae un código reconocible, el candidato
    de ESE código manda; si no hay ninguno con ese código (o el raw_name no
    trae código), se degrada a la similitud pura de siempre.

    Segundo desempate por IDIOMA (2026-08-28, revisión de una siembra
    completa contra Postgres real, docs/pendientes-motor-matching.md punto
    6): dentro de una MISMA categoría+set_code, el canónico EN y el JP
    comparten casi todo el texto salvo el sufijo -- su similarity() contra
    un raw_name da a menudo el MISMO score exacto (ej. "One Piece OP13
    Carrying On His Will" contra "...OP-13 EN" y "...OP-13 JP" empataban en
    0.469 los dos), y sin este criterio el `ORDER BY` caía al orden físico
    de la tabla, que devolvía casi siempre el EN aunque `classify_product()`
    ya hubiera detectado JP -- 61 filas reales se quedaban en needs_review
    por esto pese a tener el candidato JP correcto ya sembrado. Va ANTES
    que caja/sobre a propósito: dos idiomas del mismo set_code son
    productos distintos con precio distinto (la señal más fuerte después
    de set_code), caja/sobre es una distinción más fina dentro del mismo
    producto/idioma.

    Tercer desempate por CAJA/SOBRE (is_box_variant, mismo hallazgo):
    dentro de una MISMA categoría+set_code puede convivir la variante caja
    y la de un sobre suelto ("Premium Booster"/"Premium Booster Box", ambas
    PRB-02, misma categoría premium-collection) -- sin esto, similarity()
    puede preferir la variante equivocada.

    Fallback cross-categoría por set_code exacto (2026-08-27, caso real
    PRB02 "The Best vol.2"): el canónico se sembró en premium-collection,
    pero varias tiendas listan el mismo producto sin la palabra "Premium"
    ("Caja PRB02", "Booster Box PRB02") -- classify_product() lo clasifica
    entonces como BOOSTER_BOX, y la búsqueda de arriba, limitada a esa
    categoría, nunca encuentra el canónico real. Si dentro de la categoría
    derivada NINGÚN candidato trae el set_code exacto, se repite la
    búsqueda en TODO el catálogo filtrando solo por ese set_code -- una
    señal fuerte (PRB02, EB04, ST37... son códigos casi únicos) que no
    arrastra falsos positivos de otra familia como sí lo haría una
    búsqueda de texto libre sin categoría.

    Devuelve (candidato, es_fallback) -- es_fallback=True cuando el
    candidato viene de esta segunda búsqueda cross-categoría
    (implementacion-auto-confirmado-setcode.md 1.1, caso real: una carta
    promo individual -- categoría promo-card, vacía -- emparejaba contra un
    Booster Pack completo solo porque el código aparecía de forma decorativa
    en el nombre de la carta). Es una señal más débil a propósito, pensada
    para *sugerir* en needs_review, no para confirmar sola -- _evaluate()
    exige es_fallback=False para el auto-confirmado por set_code exacto."""
    is_box = is_box_variant(raw_name)
    cur.execute(
        """
        SELECT id, set_code, language, similarity(name_canonical, %s) AS score
        FROM product
        WHERE category_id = %s
        ORDER BY
            (set_code = %s) IS TRUE DESC,
            (language = %s) IS TRUE DESC,
            (%s IS NOT NULL AND (name_canonical ILIKE '%%box%%' OR name_canonical ILIKE '%%caja%%') = %s) DESC,
            score DESC
        LIMIT 1
        """,
        (raw_name, category_id, set_code, language, is_box, is_box),
    )
    row = cur.fetchone()
    if row is not None and set_code is not None and row[1] == set_code:
        return row, False  # ya hay un candidato con el set_code exacto en la categoría derivada

    if set_code is not None:
        cur.execute(
            """
            SELECT id, set_code, language, similarity(name_canonical, %s) AS score
            FROM product
            WHERE set_code = %s
            ORDER BY
                (language = %s) IS TRUE DESC,
                (%s IS NOT NULL AND (name_canonical ILIKE '%%box%%' OR name_canonical ILIKE '%%caja%%') = %s) DESC,
                score DESC
            LIMIT 1
            """,
            (raw_name, set_code, language, is_box, is_box),
        )
        cross_category_row = cur.fetchone()
        if cross_category_row is not None:
            return cross_category_row, True

    return row, False


def _evaluate(
    cur, category_ids: dict[str, int], classification: Classification, category_slug: Optional[str], raw_name: str,
) -> MatchOutcome:
    if classification.product_type in NOT_APPLICABLE_PRODUCT_TYPES:
        return MatchOutcome("not_applicable", None, None)

    category_id = category_ids.get(category_slug) if category_slug else None
    if category_id is None:
        # product_type reconocido por classify_product() pero sin categoría
        # sembrada todavía -- seed-catalog-app-tcg.sql desactualizado
        # respecto a CLASSIFICATION_RULES. No hay nada contra qué comparar.
        return MatchOutcome("unmatched", None, None)

    candidate, es_fallback = _best_candidate(cur, category_id, raw_name, classification.set_code,
                                              classification.language)
    if not candidate:
        return MatchOutcome("unmatched", None, None)

    product_id, set_code, language, score = candidate
    set_code_matches = classification.set_code is not None and set_code == classification.set_code

    if set_code is not None and classification.set_code is not None and set_code != classification.set_code:
        # Mismo tipo de producto pero de un LANZAMIENTO/código distinto --
        # la similitud de texto puede ser alta igualmente (ej. "Starter
        # Deck ONE PIECE FILM edition ST-05", reutilizado como plantilla en
        # muchos raw_names parecidos), pero no es el mismo producto. Nunca
        # needs_review ni confirmed en este caso. set_code en vez de
        # main_set (2026-08-27, revisión de la cola): main_set solo está
        # poblado para la familia OP por diseño (schema-postgresql-app-tcg.sql,
        # "distinto de set_code para Starter Deck/Illustration Box con
        # código propio") -- set_code sí cubre las 6 familias del catálogo
        # (OP/ST/DP/EB/PRB/DF) con el MISMO valor que main_set para OP,
        # así que este cambio no afecta ese caso y sí cierra el hueco para
        # el resto.
        #
        # "is not None" en AMBOS lados a propósito -- NO alcanza con que
        # difieran los valores tal cual (eso trataría "no sé" igual que "sé
        # que es distinto"). Encontrado simulando el impacto contra datos
        # reales (2026-08-27): Illustration Box/Devil Fruits Collection
        # nunca tienen set_code poblado en el catálogo (se sembraron desde
        # "Vol.N", sin código en mayúsculas) -- si una tienda SÍ describe el
        # mismo producto con un código reconocible ("IB-06"), comparar a
        # secas rechazaba un match que la similitud de texto sí acertaba
        # antes de este cambio. Solo se rechaza cuando AMBOS lados traen un
        # código y no coincide -- ahí sí es señal fuerte de que es otro
        # lanzamiento, no ambigüedad por falta de dato.
        return MatchOutcome("unmatched", None, None)

    if not set_code_matches and score < REVIEW_SIMILARITY_THRESHOLD:
        # El piso de similitud SOLO protege el camino de siempre (candidato
        # sin set_code exacto) -- cuando set_code_matches es True, el resto
        # de las cinco condiciones de abajo (categoría real, candidato
        # primario, idioma, cantidad) ya hacen ese trabajo con una señal más
        # fuerte que un umbral de texto (implementacion-auto-confirmado-setcode.md
        # 1.5).
        return MatchOutcome("unmatched", None, None)

    language_matches = classification.language is not None and language == classification.language
    cantidad_ok = not cantidad_es_ambigua(raw_name, category_slug)

    # Las CINCO condiciones a la vez (1.5): categoría real (ya garantizado
    # si llegamos aquí), candidato primario (no fallback cross-categoría),
    # set_code exacto, idioma exacto, cantidad no ambigua. Auto-confirma sin
    # depender del umbral de similitud de texto -- set_code exacto dentro de
    # su propia categoría es una señal más fuerte que la similitud pura.
    if set_code_matches and not es_fallback and language_matches and cantidad_ok:
        return MatchOutcome("confirmed", product_id, score)

    # Camino de siempre (pre-2026-08-27): sin las cinco condiciones, sigue
    # pudiendo confirmar por similitud de texto alta + idioma, igual que
    # antes de este cambio -- cubre los casos sin set_code detectable en
    # ninguno de los dos lados.
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
        cur.execute(
            "SELECT id, raw_name, raw_variant, raw_tags FROM store_product WHERE match_status != 'confirmed'"
        )
        rows = cur.fetchall()

        for store_product_id, raw_name, raw_variant, raw_tags in rows:
            classification, category_slug = classify_with_category(raw_name, raw_variant, raw_tags)
            outcome = _evaluate(cur, category_ids, classification, category_slug, raw_name)
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
    (product_type, set_code, language) derivados de su raw_name. Si varias
    tiendas DISTINTAS venden algo que parece el mismo producto y no hay
    ningún `product` candidato en esa categoría+set_code, es una señal de
    que falta sembrar un canónico -- pensado para que el futuro panel de
    revisión lo muestre como sugerencia de alta ("6 tiendas venden algo que
    parece OP17 Booster Box EN y no existe en el catálogo -- ¿lo creamos?").

    set_code, no main_set (2026-08-27): main_set solo se rellena para
    releases con código "OP-NN" -- Double Pack (DP-NN) e Illustration Box
    (Vol.N -> VOL-NN) tienen set_code propio pero main_set=NULL en sus
    canónicos (verificado en la BBDD real), así que agrupar/comprobar por
    main_set generaba falsos positivos: 10 de 18 sugerencias eran productos
    que YA existían en el catálogo, solo que con main_set NULL. set_code es
    el campo que matcher._evaluate()/_best_candidate() ya usan como
    identidad real de emparejamiento -- aquí se sigue el mismo criterio.

    No crea nada automáticamente -- solo reporta. Ignora LOTE_CARTAS/OTROS
    (nunca tienen canónico) y agrupaciones con menos de `min_stores` tiendas
    distintas (ruido de una sola tienda, no una señal fuerte)."""
    category_ids = _category_ids(conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT store_id, raw_name, raw_variant, raw_tags FROM store_product WHERE match_status != 'confirmed'"
        )
        rows = cur.fetchall()

        groups: dict[tuple, set] = defaultdict(set)
        main_sets: dict[tuple, str | None] = {}
        for store_id, raw_name, raw_variant, raw_tags in rows:
            classification = classify_product(raw_name, raw_variant, raw_tags)
            if classification.product_type in NOT_APPLICABLE_PRODUCT_TYPES:
                continue
            key = (classification.product_type, classification.set_code, classification.language)
            groups[key].add(store_id)
            main_sets[key] = classification.main_set  # solo para mostrar/prellenar, no para la comprobación

        suggestions = []
        for (product_type, set_code, language), store_ids in groups.items():
            if len(store_ids) < min_stores:
                continue

            category_slug = PRODUCT_TYPE_TO_CATEGORY_SLUG.get(product_type)
            category_id = category_ids.get(category_slug) if category_slug else None
            has_candidate = False
            if category_id is not None:
                cur.execute(
                    "SELECT 1 FROM product WHERE category_id = %s AND set_code IS NOT DISTINCT FROM %s LIMIT 1",
                    (category_id, set_code),
                )
                has_candidate = cur.fetchone() is not None

            # Mismo fallback cross-categoría que _best_candidate() (caso
            # PRB02): si no hay candidato en la categoría derivada pero SÍ
            # existe un canónico con ese set_code exacto en OTRA categoría,
            # no es un hueco real del catálogo -- es una tienda que
            # clasificó el producto distinto a como se sembró.
            if not has_candidate and set_code is not None:
                cur.execute("SELECT 1 FROM product WHERE set_code = %s LIMIT 1", (set_code,))
                has_candidate = cur.fetchone() is not None

            if not has_candidate:
                suggestions.append({
                    "product_type": product_type,
                    "set_code": set_code,
                    "main_set": main_sets[(product_type, set_code, language)],
                    "language": language,
                    "store_count": len(store_ids),
                })

    return sorted(suggestions, key=lambda s: s["store_count"], reverse=True)

"""Motor de matching: store_product.raw_name (ya en BBDD) -> product
canónico. Bloque C de cambios-necesarios-scraper.md.

No es aprendizaje automático (C.4): classify_product() es determinista
(reglas fijas de texto, las mismas que ya usa el scraper) y pg_trgm es
similitud de texto sobre name_canonical, no un modelo que mejora con el uso.
Si el panel de revisión revela un patrón de error recurrente, la corrección
es editar el pipeline de reconocimiento a mano, no reentrenar nada.

Arquitectura (docs/propuestas/guia_nuevo_matcher.md): separa **Evidence
Builder** (`build_evidence()`, construye un `MatchEvidence` con banderas:
`exact_name_match`, `set_code_match`, `language_match`, `packaging_match`,
`similarity_score`, `is_fallback_candidate`, `quantity_ambiguous`,
`is_single_sku_category`) de **Decision Policy** (`decide()`, tabla de
decisión pura sobre esas banderas, testeable sin BBDD) -- antes ambas cosas
vivían mezcladas en `_evaluate()`, con varios caminos de decisión implícitos
entrelazados.

Umbrales (aprobados 2026-08-26, valores de partida) y las condiciones del
auto-confirmado por set_code (2026-08-27) y por packaging exacto (NUEVO,
sustituye a la antigua separación caja/sobre/case por categoría):
- Coincidencia EXACTA de nombre (`name_canonical` contra `raw_name`,
  case-insensitive) -> 'confirmed' directo, la señal más fuerte posible, sin
  mirar nada más.
- Camino rápido: candidato PRIMARIO (no fallback cross-categoría, ver
  `_best_candidate`) con set_code + product_type + language + packaging
  exactos y cantidad no ambigua (ver `shared.classify.cantidad_es_ambigua`)
  -> 'confirmed' automático, sin depender de la similitud de texto.
- Categoría con un único SKU posible en TODO el catálogo (LEARN_DECK/
  DICE_ACCESSORY históricamente) -> 'confirmed' si el candidato es primario
  y el idioma no contradice explícitamente.
- Camino de siempre (sin las condiciones de arriba, típicamente sin
  set_code detectable en algún lado): set_code + product_type coinciden y
  similarity > 0.6 con idioma exacto -> 'confirmed'; similarity en
  [0.35, 0.6) (o idioma no coincide/no detectado) -> 'needs_review'.
- set_code NO coincide (aunque el product_type sí -- probablemente el mismo
  tipo de producto pero de OTRO lanzamiento, similitud de texto engañosa),
  o (sin set_code exacto) similarity < 0.35 -> 'unmatched'.

LOTE_CARTAS/PROMO_CARD/MYSTERY_PACK/DICE_ACCESSORY/OTROS (Fase 0 del
pipeline + catch-all) nunca entran en este pipeline -- se marcan
'not_applicable' directamente, sin gastar una consulta de similitud
(NOT_APPLICABLE_PRODUCT_TYPES).

El top-3 de candidatos (C.3) NO se guarda aquí -- se calcula en caliente en
el futuro endpoint GET /matches/pending vía `ORDER BY
similarity(name_canonical, raw_name) DESC LIMIT 3` sobre
idx_product_name_trgm. Este módulo solo decide y persiste el match_status
y, si corresponde, product_id + match_confidence del MEJOR candidato.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from shared.classify import (
    NOT_APPLICABLE_PRODUCT_TYPES,
    PRODUCT_TYPE_TO_CATEGORY_SLUG,
    SET_CODE_PREFIXES,
    cantidad_es_ambigua,
    classify_product,
    classify_with_category,
)
from shared.domain import Classification

CONFIRMED_SIMILARITY_THRESHOLD = 0.6
REVIEW_SIMILARITY_THRESHOLD = 0.35

# Coincide con el ENUM product_language del esquema (schema-postgresql-app-tcg.sql)
# -- _detect_language() en shared/classify.py puede devolver otros valores
# (ej. "KR", coreano) que el esquema no admite; ver _best_candidate() para
# el motivo de por qué hace falta esta lista aquí.
_VALID_DB_LANGUAGES = {"EN", "JP", "ES"}


@dataclass
class MatchOutcome:
    match_status: str
    product_id: Optional[int]
    match_confidence: Optional[float]


def _category_ids(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT slug, id FROM category")
        return dict(cur.fetchall())


def _single_sku_categories(conn) -> set[int]:
    """Categorías con exactamente 1 producto canónico sembrado -- no hay
    nada con qué confundirlo. Se recalcula en cada run_matching() en vez de
    mantenerse como lista fija a mano: si se siembra un segundo SKU en una
    de estas categorías más adelante, la siguiente pasada deja de
    auto-confirmar sola, sin tocar este código (mismo principio que
    category_ids, calculado en caliente)."""
    with conn.cursor() as cur:
        cur.execute("SELECT category_id FROM product GROUP BY category_id HAVING count(*) = 1")
        return {row[0] for row in cur.fetchall()}


def _best_candidate(
    cur,
    category_id: int,
    raw_name: str,
    set_code: Optional[str] = None,
    language: Optional[str] = None,
    packaging: Optional[str] = None,
):
    """Top-1 candidato dentro de la categoría -- basta con 1 para decidir el
    match_status; el top-3 completo (C.3) es responsabilidad del futuro
    endpoint de revisión, no de este matcher.

    Prioriza set_code EXACTO sobre similitud de texto pura: varios
    raw_names de la misma familia comparten casi todo el texto salvo el
    código ("Starter Deck: Red Monkey.D.Luffy ST-31" vs "...ST-30"), así que
    similarity() por sí sola puede preferir un candidato genérico/de otro
    código sobre el correcto. Cuando el raw_name trae un código reconocible,
    el candidato de ESE código manda; si no hay ninguno con ese código (o el
    raw_name no trae código), se degrada a la similitud pura de siempre.

    Segundo desempate por IDIOMA: dentro de una MISMA categoría+set_code, el
    canónico EN y el JP comparten casi todo el texto salvo el sufijo -- su
    similarity() contra un raw_name da a menudo el MISMO score exacto, y sin
    este criterio el `ORDER BY` caía al orden físico de la tabla. Va ANTES
    que packaging a propósito: dos idiomas del mismo set_code son productos
    distintos con precio distinto (la señal más fuerte después de
    set_code), packaging es una distinción más fina dentro del mismo
    producto/idioma.

    Tercer desempate por PACKAGING (columna `product.packaging`, NUEVO --
    sustituye a la antigua comparación de texto `is_box_variant(raw_name)`
    contra un ILIKE '%box%'/'%caja%'): dentro de una MISMA categoría+set_code
    puede convivir la variante sobre y la de caja completa (ONE_PIECE con
    packaging 'sobre'/'display'/'case', o "Premium Booster"/"Premium
    Booster Box" ambas PRB-02 en premium-booster-box) -- sin esto,
    similarity() puede preferir la variante equivocada. Cuando `packaging`
    es None (família sin esa dimensión, ej. Illustration Box), la
    comparación `(packaging = NULL) IS TRUE` es NULL/false para toda fila
    por igual, así que no discrimina nada -- no hace falta ramificar.

    Fallback cross-categoría por set_code exacto (caso real PRB02 "The Best
    vol.2"): el canónico se sembró en premium-booster-box, pero varias
    tiendas listan el mismo producto sin la palabra "Premium" --
    classify_product() lo clasifica entonces como ONE_PIECE, y la búsqueda
    de arriba, limitada a esa categoría, nunca encuentra el canónico real.
    Si dentro de la categoría derivada NINGÚN candidato trae el set_code
    exacto, se repite la búsqueda en TODO el catálogo filtrando solo por ese
    set_code -- una señal fuerte que no arrastra falsos positivos de otra
    familia como sí lo haría una búsqueda de texto libre sin categoría.

    Devuelve (candidato, es_fallback) -- candidato es
    (id, name_canonical, set_code, language, packaging, score).
    es_fallback=True cuando el candidato viene de la segunda búsqueda
    cross-categoría (caso real: una carta promo individual -- categoría
    promo-card, vacía -- emparejaba contra un Booster Pack completo solo
    porque el código aparecía de forma decorativa en el nombre de la
    carta). Es una señal más débil a propósito, pensada para *sugerir* en
    needs_review, no para confirmar sola -- decide() exige
    is_fallback_candidate=False para el auto-confirmado por set_code
    exacto."""
    # classify_product() puede detectar idiomas que el ENUM product_language
    # de Postgres no admite (ej. "KR", coreano -- ver _detect_language en
    # shared/classify.py) -- comparar ese valor tal cual contra la columna
    # `language` revienta la query en Postgres (psycopg2.errors.
    # InvalidTextRepresentation: invalid input value for enum
    # product_language), encontrado real contra un raw_name real
    # ("...CAJA SOBRES COREANO"). Se trata como "idioma no detectado" para
    # ESTA consulta -- degradación segura, mismo comportamiento que
    # language=None ya tenía (nunca hay un canónico coreano con el que
    # desempatar de todas formas).
    if language not in _VALID_DB_LANGUAGES:
        language = None
    cur.execute(
        """
        SELECT id, name_canonical, set_code, language, packaging,
               similarity(name_canonical, %s) AS score
        FROM product
        WHERE category_id = %s
        ORDER BY
            (set_code = %s) IS TRUE DESC,
            (language = %s) IS TRUE DESC,
            (packaging = %s) IS TRUE DESC,
            score DESC
        LIMIT 1
        """,
        (raw_name, category_id, set_code, language, packaging),
    )
    row = cur.fetchone()
    if row is not None and set_code is not None and row[2] == set_code:
        return row, False  # ya hay un candidato con el set_code exacto en la categoría derivada

    # SOLO para prefijos REALMENTE asignados por Bandai (SET_CODE_PREFIXES,
    # ver shared/classify.py) -- "VOL{NN}" es un pseudo-código inventado por
    # este proyecto y reutilizado de forma independiente por Illustration
    # Box/Sleeves/Playmat/Premium Card Collection, así que un "VOL09" de una
    # família no tiene NADA que ver con el "VOL09" de otra. Sin esta guarda
    # (bug real 2026-08-30), este fallback sugería "Official Sleeves 9" como
    # candidato de una fila real de "Illustration Box IB-09".
    if set_code is not None and set_code.startswith(SET_CODE_PREFIXES):
        cur.execute(
            """
            SELECT id, name_canonical, set_code, language, packaging,
                   similarity(name_canonical, %s) AS score
            FROM product
            WHERE set_code = %s
            ORDER BY
                (language = %s) IS TRUE DESC,
                (packaging = %s) IS TRUE DESC,
                score DESC
            LIMIT 1
            """,
            (raw_name, set_code, language, packaging),
        )
        cross_category_row = cur.fetchone()
        if cross_category_row is not None:
            return cross_category_row, True

    return row, False


@dataclass
class MatchEvidence:
    """Banderas puras sobre un candidato -- sin BBDD, testeable a secas.
    `language_known`/`is_single_sku_category` existen porque "el idioma no
    se pudo detectar" y "el idioma se detectó pero no coincide" son señales
    distintas para el camino de categoría-de-SKU-único (ahí, sin señal se
    trata como "no contradicho", no como "no coincide")."""

    exact_name_match: bool
    set_code_match: Optional[bool]
    language_match: bool
    language_known: bool
    packaging_match: bool
    similarity_score: Optional[float]
    is_fallback_candidate: bool
    quantity_ambiguous: bool
    is_single_sku_category: bool


def build_evidence(
    classification: Classification,
    candidate,
    es_fallback: bool,
    raw_name: str,
    is_single_sku_category: bool,
) -> MatchEvidence:
    _product_id, name_canonical, set_code, language, packaging, score = candidate

    if classification.set_code is None or set_code is None:
        set_code_match = None
    else:
        set_code_match = set_code == classification.set_code

    language_known = classification.language is not None
    language_match = language_known and language == classification.language

    # packaging=None en la classification (família sin esa dimensión, ej.
    # Illustration Box/Playmat/Sleeves) -> "no contradicho", no "no
    # coincide" -- mismo criterio que language_known para el camino de
    # SKU único, aplicado aquí al camino rápido de 5 condiciones.
    packaging_match = classification.packaging is None or packaging == classification.packaging

    exact_name_match = name_canonical.strip().lower() == raw_name.strip().lower()

    quantity_ambiguous = cantidad_es_ambigua(raw_name, classification.product_type, classification.packaging)

    return MatchEvidence(
        exact_name_match=exact_name_match,
        set_code_match=set_code_match,
        language_match=language_match,
        language_known=language_known,
        packaging_match=packaging_match,
        similarity_score=score,
        is_fallback_candidate=es_fallback,
        quantity_ambiguous=quantity_ambiguous,
        is_single_sku_category=is_single_sku_category,
    )


def decide(evidence: MatchEvidence) -> str:
    """Tabla de decisión pura -- sin BBDD, sin efectos. Orden importa: cada
    regla es un `if...return`, la primera que aplica gana."""
    if evidence.exact_name_match:
        return "confirmed"

    if evidence.set_code_match is False:
        # Mismo tipo de producto pero de un LANZAMIENTO/código distinto --
        # la similitud de texto puede ser alta igualmente, pero no es el
        # mismo producto. Nunca needs_review ni confirmed en este caso.
        return "unmatched"

    # Categoría con un único SKU posible en TODO el catálogo -- no hay nada
    # con qué confundirlo, así que ni el umbral de similitud ni "cantidad
    # ambigua" aportan nada aquí, solo bloquean un match que ya es
    # inequívoco por construcción. Exige candidato primario (mismo motivo
    # que el camino rápido de abajo) y que el idioma no contradiga
    # explícitamente -- si la tienda SÍ marca JP y el único sembrado es EN,
    # mejor needs_review que asignar mal el idioma.
    if evidence.is_single_sku_category and not evidence.is_fallback_candidate and (
        not evidence.language_known or evidence.language_match
    ):
        return "confirmed"

    # El piso de similitud SOLO protege el camino de siempre (candidato sin
    # set_code exacto) -- cuando hay set_code_match, el resto de las
    # condiciones de abajo (categoría real, candidato primario, idioma,
    # packaging, cantidad) ya hacen ese trabajo con una señal más fuerte que
    # un umbral de texto.
    if evidence.set_code_match is None and evidence.similarity_score is not None:
        if evidence.similarity_score < REVIEW_SIMILARITY_THRESHOLD:
            return "unmatched"

    # Las condiciones a la vez: candidato primario (no fallback
    # cross-categoría), set_code exacto, idioma exacto, packaging exacto (o
    # no aplicable), cantidad no ambigua. Auto-confirma sin depender del
    # umbral de similitud de texto -- set_code exacto dentro de su propia
    # categoría es una señal más fuerte que la similitud pura.
    if (
        evidence.set_code_match
        and not evidence.is_fallback_candidate
        and evidence.language_match
        and evidence.packaging_match
        and not evidence.quantity_ambiguous
    ):
        return "confirmed"

    # Camino de siempre: sin las condiciones de arriba, sigue pudiendo
    # confirmar por similitud de texto alta + idioma -- cubre los casos sin
    # set_code detectable en ninguno de los dos lados.
    if (
        evidence.language_match
        and evidence.similarity_score is not None
        and evidence.similarity_score > CONFIRMED_SIMILARITY_THRESHOLD
    ):
        return "confirmed"

    return "needs_review"


def _evaluate(
    cur,
    category_ids: dict[str, int],
    classification: Classification,
    category_slug: Optional[str],
    raw_name: str,
    single_sku_categories: set[int] = frozenset(),
) -> MatchOutcome:
    if classification.product_type in NOT_APPLICABLE_PRODUCT_TYPES:
        return MatchOutcome("not_applicable", None, None)

    category_id = category_ids.get(category_slug) if category_slug else None
    if category_id is None:
        # product_type reconocido por classify_product() pero sin categoría
        # sembrada todavía -- seed-catalog-app-tcg.sql desactualizado. No
        # hay nada contra qué comparar.
        return MatchOutcome("unmatched", None, None)

    candidate, es_fallback = _best_candidate(
        cur, category_id, raw_name, classification.set_code, classification.language, classification.packaging
    )
    if not candidate:
        return MatchOutcome("unmatched", None, None)

    evidence = build_evidence(
        classification,
        candidate,
        es_fallback,
        raw_name,
        is_single_sku_category=category_id in single_sku_categories,
    )
    status = decide(evidence)
    product_id = candidate[0]
    score = candidate[-1]

    if status == "confirmed":
        return MatchOutcome("confirmed", product_id, score)
    if status == "unmatched":
        return MatchOutcome("unmatched", None, None)
    return MatchOutcome("needs_review", None, None)


def run_matching(conn) -> dict[str, int]:
    """Reevalúa todos los store_product que no estén ya 'confirmed' (una
    confirmación es una decisión ya tomada -- C.4, esto no es ML, no se
    revierte sola) y actualiza su match_status/product_id/match_confidence.

    Deliberadamente reevalúa también los que ya están 'not_applicable' o
    'needs_review': si el pipeline de reconocimiento cambia a mano (C.4) o
    se siembra un producto canónico nuevo, la siguiente pasada debe poder
    recalcular sin intervención manual adicional."""
    category_ids = _category_ids(conn)
    if not category_ids:
        raise RuntimeError(
            "La tabla category está vacía -- aplica seed-catalog-app-tcg.sql "
            "antes de correr el matcher (ver D.2)."
        )
    single_sku_categories = _single_sku_categories(conn)

    counts: dict[str, int] = defaultdict(int)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, raw_name, raw_variant, raw_tags FROM store_product WHERE match_status != 'confirmed'"
        )
        rows = cur.fetchall()

        for store_product_id, raw_name, raw_variant, raw_tags in rows:
            classification, category_slug = classify_with_category(raw_name, raw_variant, raw_tags)
            outcome = _evaluate(cur, category_ids, classification, category_slug, raw_name, single_sku_categories)
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
    (product_type, set_code, language, packaging) derivados de su raw_name.
    Si varias tiendas DISTINTAS venden algo que parece el mismo producto y
    no hay ningún `product` candidato en esa categoría+set_code+packaging,
    es una señal de que falta sembrar un canónico -- pensado para que el
    futuro panel de revisión lo muestre como sugerencia de alta ("6 tiendas
    venden algo que parece OP17 Booster Box EN y no existe en el catálogo --
    ¿lo creamos?").

    `packaging` en la clave de agrupación (NUEVO): con ONE_PIECE/
    EXTRA_BOOSTER/PREMIUM_BOOSTER_BOX unificando sobre/display/case en una
    única categoría, agrupar solo por (product_type, set_code, language)
    escondería que falta sembrar la variante 'display' aunque la 'sobre' ya
    exista -- son productos de precio completamente distinto.

    set_code, no main_set: main_set solo se rellena para releases con
    código "OP-NN" propio -- Double Pack (DP-NN) tiene set_code propio pero
    main_set se deriva por separado (tabla DP<->OP), así que agrupar por
    main_set generaría falsos positivos con productos que YA existen en el
    catálogo. set_code es el campo que _evaluate()/_best_candidate() ya usan
    como identidad real de emparejamiento -- aquí se sigue el mismo
    criterio.

    No crea nada automáticamente -- solo reporta. Ignora
    NOT_APPLICABLE_PRODUCT_TYPES (nunca tienen canónico) y agrupaciones con
    menos de `min_stores` tiendas distintas (ruido de una sola tienda, no
    una señal fuerte)."""
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
            key = (classification.product_type, classification.set_code, classification.language, classification.packaging)
            groups[key].add(store_id)
            main_sets[key] = classification.main_set  # solo para mostrar/prellenar, no para la comprobación

        suggestions = []
        for (product_type, set_code, language, packaging), store_ids in groups.items():
            if len(store_ids) < min_stores:
                continue

            category_slug = PRODUCT_TYPE_TO_CATEGORY_SLUG.get(product_type)
            category_id = category_ids.get(category_slug) if category_slug else None
            has_candidate = False
            if category_id is not None:
                cur.execute(
                    """
                    SELECT 1 FROM product
                    WHERE category_id = %s AND set_code IS NOT DISTINCT FROM %s
                          AND packaging IS NOT DISTINCT FROM %s
                    LIMIT 1
                    """,
                    (category_id, set_code, packaging),
                )
                has_candidate = cur.fetchone() is not None

            # Mismo fallback cross-categoría que _best_candidate() (caso
            # PRB02): si no hay candidato en la categoría derivada pero SÍ
            # existe un canónico con ese set_code exacto en OTRA categoría,
            # no es un hueco real del catálogo -- es una tienda que
            # clasificó el producto distinto a como se sembró. SOLO para
            # prefijos REALMENTE asignados por Bandai (SET_CODE_PREFIXES) --
            # ver _best_candidate() para el porqué ("VOL{NN}" es un
            # pseudo-código reutilizado de forma independiente por varias
            # famílias sin relación entre sí).
            if not has_candidate and set_code is not None and set_code.startswith(SET_CODE_PREFIXES):
                cur.execute(
                    "SELECT 1 FROM product WHERE set_code = %s AND packaging IS NOT DISTINCT FROM %s LIMIT 1",
                    (set_code, packaging),
                )
                has_candidate = cur.fetchone() is not None

            if not has_candidate:
                suggestions.append(
                    {
                        "product_type": product_type,
                        "set_code": set_code,
                        "main_set": main_sets[(product_type, set_code, language, packaging)],
                        "language": language,
                        "packaging": packaging,
                        "store_count": len(store_ids),
                    }
                )

    return sorted(suggestions, key=lambda s: s["store_count"], reverse=True)

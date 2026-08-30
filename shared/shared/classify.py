"""Lógica de negocio pura: clasificación de producto (Recognition Pipeline) y
parseo de precios. Sin red, sin BBDD, sin logging con estado -- solo
funciones de texto sobre datos ya en memoria, compartidas por el scraper, el
matcher (bloque C), la siembra del catálogo oficial (seed_official_catalog.py)
y el panel de matching de api/ (services/matches.py).

Vive en shared/ (no en store_monitor/) por el mismo motivo que domain.py --
ver decisión de arquitectura sobre el acoplamiento entre api/ y
store_monitor/ (patrón Shared Kernel de DDD).

Arquitectura (docs/propuestas/guia_nuevo_matcher.md, "Recognition Pipeline"):
Fase 0 (Gate) -> Fase 1 (Idioma) -> Fase 2 (Tipo de producto). Primer match
gana en cada fase. Las família de Fase 0 (LOTE_CARTAS/PROMO_CARD/
MYSTERY_PACK/DICE_ACCESSORY) cortan el pipeline entero -- no tienen
canónico de producto sellado razonable con el que comparar precio, se
marcan not_applicable sin gastar consulta de similitud (ver
NOT_APPLICABLE_PRODUCT_TYPES, y matcher._evaluate()).

`packaging` (display/case/sobre, ver Classification en domain.py) sustituye
a la antigua separación caja/sobre/case POR CATEGORÍA (antes BOOSTER_BOX/
BOOSTER_PACK/BOOSTER_CASE eran tres categorías distintas) por un campo de
primera clase calculado dentro de Fase 2."""

from __future__ import annotations

import html
import re
from typing import Optional

from .domain import Classification

# ---------------------------------------------------------------------------
# Constantes compartidas entre fases
# ---------------------------------------------------------------------------

# Prefijos de código de set REALMENTE usados en el catálogo (verificado
# contra product.set_code) -- lista blanca explícita, no "cualquier 2-3
# mayúsculas seguidas de dígitos". Un patrón genérico capturaba palabras
# incidentales en mayúsculas que no son ningún código de set (ej. "VOL2" en
# "THE BEST VOL2 - PRB02", cogido antes de llegar al código real). Añadir un
# prefijo nuevo (una línea aquí) es la única forma soportada de ampliar
# esto -- nunca aflojar el patrón a algo genérico otra vez.
#
# Pública (sin "_") a propósito -- store_monitor/matcher.py y
# api/services/matches.py la reutilizan para guardar su fallback
# cross-categoría por set_code (ver _best_candidate()/_top_candidates()):
# estos SÍ son códigos asignados por Bandai, globalmente únicos en todo el
# catálogo real, así que un match cross-categoría por uno de ellos es una
# señal fuerte de verdad. "VOL{NN}" NUNCA debe tratarse igual -- es un
# pseudo-código INVENTADO por este proyecto, y reutilizado de forma
# independiente por CUATRO famílias distintas sin relación entre sí
# (Illustration Box, Sleeves, Playmat, Premium Card Collection) -- su
# "VOL09" no tiene nada que ver con el "VOL09" de otra família, a
# diferencia de "PRB02" o "OP17". Bug real corregido 2026-08-30: el
# fallback cross-categoría no distinguía esto, y sugería "Official Sleeves
# 9" como candidato de una fila real de "Illustration Box IB-09".
SET_CODE_PREFIXES = ("OP", "ST", "DP", "EB", "PRB", "DF")

# Rango de códigos ("ST-15 - ST-20", "[ST-31]~[ST-36]") -- un pack
# promocional ligado a TODO un lote de mazos/sets, no a uno solo; extraer el
# primer extremo sería un código inventado. Reutilizada como guarda dentro
# de las funciones _extract_*_code de Fase 2. Corchetes opcionales en AMBOS
# extremos, mismo prefijo real en los dos lados vía \1 (backreference) --
# bug real corregido 2026-08-30: antes cada lado comprobaba la lista de
# prefijos por separado (sin exigir que fuera EL MISMO), así que también
# bloqueaba designadores DOBLES reales de un único producto con dos códigos
# distintos ("OP15-EB04", "DOUBLE PACK DP09 - OP12 ...") como si fueran un
# rango -- nunca lo son, un rango real siempre repite el mismo prefijo.
_RANGO_CODIGOS_RE = re.compile(
    rf"\[?({'|'.join(SET_CODE_PREFIXES)})[\s-]?\d{{1,3}}\]?\s*[-~]\s*"
    rf"\[?\1[\s-]?\d{{1,3}}\]?",
    re.IGNORECASE,
)

# Código de acompañamiento -- extracción genérica para família SIN
# tratamiento especial: Fase 0 (LOTE_CARTAS/PROMO_CARD conservan el set de
# origen de la carta suelta, aunque nunca se use para comparar precio) y el
# catch-all OTROS de Fase 2 (mismo principio, generalizado). SIN el
# lookahead que sí llevan las regex de família de Fase 2 (ver más abajo) a
# propósito: necesita seguir siendo permisiva para capturar el set de una
# carta suelta gradeada con numeración propia (p.ej. "Rebecca (OP10-058)"
# -> "OP10") -- lo contrario de lo que esas regex existen para evitar.
_GENERIC_CODE_RE = re.compile(
    rf"\b({'|'.join(SET_CODE_PREFIXES)})[\s-]?0*(\d{{1,3}})\b", re.IGNORECASE
)


def _extract_generic_code(name: str) -> Optional[str]:
    m = _GENERIC_CODE_RE.search(name)
    return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else None


_LOOKUP_NON_ALNUM_RE = re.compile(r"[^a-z0-9']+")


def _normalize_for_lookup(text: str) -> str:
    """Normaliza texto para comparar contra las tablas de lookup de abajo
    (título de release / personaje / edición) -- decodifica entidades HTML
    (visto real en varias tiendas: "&#8217;"/"&amp;" sin decodificar en el
    raw_name), minúsculas, quita apóstrofos (' y la comilla tipográfica que
    deja html.unescape) en vez de convertirlos en espacio ("Kami's" ->
    "kamis", no "kami s"), y colapsa cualquier otro separador a un solo
    espacio."""
    unescaped = html.unescape(text).lower().replace("’", "'").replace("'", "")
    return _LOOKUP_NON_ALNUM_RE.sub(" ", unescaped).strip()


def _lookup_code(normalized_name: str, table: tuple[tuple[str, str], ...]) -> Optional[str]:
    for keyword, code in table:
        if keyword in normalized_name:
            return code
    return None


def _detect_language(text: Optional[str]) -> Optional[str]:
    """Detecta idioma en un fragmento de texto (nombre de producto o título de
    variante). El chequeo de "EN" es case-sensitive a propósito (sobre el texto
    ORIGINAL, no en minúsculas) para no confundir con la preposición española
    "en", que aparece constantemente en descripciones de productos."""
    if not text:
        return None
    lower = text.lower()
    if "japones" in lower or "japonés" in lower or re.search(r"\bJP\b", text):
        return "JP"
    if "coreano" in lower:
        return "KR"
    if "ingles" in lower or "inglés" in lower or re.search(r"\bEN\b", text) or "- en" in lower:
        return "EN"
    if "castellano" in lower or "español" in lower:
        return "ES"
    return None


# ---------------------------------------------------------------------------
# Fase 0 -- Gate. Corta el pipeline entero -- nada de lo que sigue se evalúa
# si esto dispara. LOTE_CARTAS/PROMO_CARD/MYSTERY_PACK/DICE_ACCESSORY
# comparten la misma naturaleza: unidades/lotes individuales o accesorios
# sin producto sellado canónico equivalente contra el que comparar precio.
# ---------------------------------------------------------------------------

_PROMO_CODE_RE = re.compile(r"\bP-?\d{2,4}\b", re.IGNORECASE)

# "Don!!" suelto + código DP-NN, SIN ninguna palabra que confirme que es el
# producto SELLADO completo: "Don!! (DP10 Map) - One Piece Products (DON!!)"
# es la carta suelta promocional de regalo que viene DENTRO de un Double
# Pack Set, no el pack sellado en sí. "set"/"pack"/"caja"/"box"/"sobre(s)"
# en el mismo texto confirma que SÍ es el sellado completo ("Special DON!!
# Card Pack DP-06" sigue siendo DOUBLE_PACK).
_DON_CARD_RE = re.compile(r"\bdon!!", re.IGNORECASE)
_SEALED_PRODUCT_CONTEXT_RE = re.compile(r"\bset\b|\bpack\b|\bcaja\b|\bbox\b|\bsobres?\b", re.IGNORECASE)


def _classify_pass(name: str, variant_title: Optional[str], tags: Optional[str]) -> Classification:
    """Una pasada del pipeline sobre name+variant(+tags opcional). Fase 0 +
    Fase 1 + Fase 2 completas."""
    text = f"{name} {variant_title or ''} {tags or ''}".lower()
    name_variant_text = f"{name.lower()} {variant_title.lower() if variant_title else ''}"

    # Entrada de TODAS las funciones de extracción de código de Fase 2 (y de
    # LOTE_CARTAS/PROMO_CARD/OTROS en Fase 0) -- no solo `name`. Tiendas
    # reales (sobre todo Shopify) meten a menudo el código/versión/personaje
    # en variant_title en vez de en el nombre principal (ej. name="Official
    # Sleeves", variant="Card Sleeves Monkey D. Luffy Vol.01 ..."); sin esto
    # esas filas nunca podían llevar set_code por mucho que el dato
    # estuviera presente en el raw_variant. `name` va primero a propósito --
    # con las regex/tablas basadas en `.search()`/substring, un código real
    # en `name` sigue ganando sobre lo que haya en variant_title si ambos
    # aplicasen.
    name_variant = f"{name} {variant_title or ''}"

    # FASE 1 (calculada antes de Fase 0, no después) -- idioma SOLO de
    # name+variant, NUNCA de tags: reutilizada por Fase 0 y Fase 2 por
    # igual. Calcularla por rama (como en el primer borrador del diseño)
    # perdía la excepción "non-english" para LOTE_CARTAS/PROMO_CARD; leerla
    # de `text` (con tags) para la excepción "non-english" dejaba que un
    # raw_tags ruidoso cambiara el idioma entre la pasada sin tags y la
    # pasada con tags del two-pass de más abajo -- las tags NUNCA participan
    # en la detección de idioma, solo en la de tipo (Fase 0/Fase 2).
    language = _detect_language(name) or _detect_language(variant_title)
    if language is None and "non-english" not in name_variant_text and "non english" not in name_variant_text:
        language = "EN"

    # FASE 0 -- Gate
    if any(kw in text for kw in ("cgc", "psa", "bgs", "lote")):
        return Classification("LOTE_CARTAS", _extract_generic_code(name_variant), language, None)

    if any(
        kw in text
        for kw in ("promo card", "carta promo", "carta promocional", "promo pack", "promotion pack")
    ) or _PROMO_CODE_RE.search(name):
        return Classification("PROMO_CARD", _extract_generic_code(name_variant), language, None)

    if _DON_CARD_RE.search(text) and not _SEALED_PRODUCT_CONTEXT_RE.search(text):
        dp_match = _DOUBLE_PACK_SET_CODE_RE.search(text)
        code = f"DP{int(dp_match.group(1)):02d}" if dp_match else None
        return Classification("PROMO_CARD", code, language, None)

    if any(kw in text for kw in ("mystery pack", "mystery box")):
        return Classification("MYSTERY_PACK", None, language, None)

    if "dice" in text:
        return Classification("DICE_ACCESSORY", None, language, None)

    # FASE 2 -- Tipo de producto. El orden es la parte crítica del diseño:
    # primer match gana. Cada família con código propio comprueba solo SU
    # PROPIA regex/tabla -- nunca la de otra família (tablas exclusivas por
    # línea, no una tabla compartida con filtro a posteriori).
    if _DF_CODE_RE.search(text) or any(kw in text for kw in ("devil fruits collection", "fruta del diablo")):
        return Classification("DEVIL_FRUITS_COLLECTION", _extract_df_code(name_variant), language, None)

    if _IB_CODE_RE.search(text) or "illustration box" in text or "caja de ilustraciones" in text:
        return Classification("ILLUSTRATION_BOX", _extract_illustration_box_code(name_variant), language, None)

    if "playmat" in text or "tapete" in text:
        code = _extract_volume_code(name_variant, "VOL")
        if code is None:
            code = _lookup_code(_normalize_for_lookup(name_variant), _PLAYMAT_CHARACTER_CODES)
        return Classification("PLAYMAT", code, language, None)

    if "sleeve" in text or "funda" in text:
        return Classification("SLEEVES", _extract_sleeves_code(name_variant), language, None)

    if "premium card collection" in text:
        code = _extract_volume_code(name_variant, "VOL")
        if code is None:
            code = _lookup_code(_normalize_for_lookup(name_variant), _PREMIUM_CARD_COLLECTION_EDITION_CODES)
        if code is None:
            code = _lookup_code(_normalize_for_lookup(name_variant), _PREMIUM_CARD_COLLECTION_EDITION_CODES_SHORT)
        return Classification("PREMIUM_CARD_COLLECTION", code, language, None)

    # "learn together"/"learn to play"/"aprende a jugar" SIEMPRE antes que
    # el patrón genérico de Starter Deck -- un raw_name real puede contener
    # AMBAS keywords a la vez ("LEARN TOGETHER DECK SET - STARTER DECKS ONE
    # PIECE"), y este debe ganar. Código fijo "LD01" (2026-08-30): es el
    # ÚNICO release de esta línea en el catálogo oficial (data/
    # one_piece_tcg_products.json solo tiene una entrada "Learn Together
    # Deck Set", sin variantes) -- antes quedaba sin código nunca podía
    # confirmar por set_code exacto pese a ser un producto inequívoco. Si el
    # texto trae el código LD-NN explícito, se respeta ese en vez de asumir.
    if any(kw in text for kw in ("learn together", "learn to play", "aprende a jugar")):
        pkg = _detect_packaging("STARTER_DECK", text)
        m = _LEARN_TOGETHER_CODE_RE.search(name_variant)
        code = f"LD{int(m.group(1)):02d}" if m else "LD01"
        return Classification("STARTER_DECK", code, language, None, packaging=pkg)

    if _ST_CODE_RE.search(text) or "starter deck" in text or "ultra deck" in text or "mazo" in text:
        pkg = _detect_packaging("STARTER_DECK", text)
        code = _extract_st_code(name_variant)
        if code is None:
            code = _lookup_code(_normalize_for_lookup(name_variant), _STARTER_DECK_CHARACTER_CODES)
        return Classification("STARTER_DECK", code, language, None, packaging=pkg)

    if _DOUBLE_PACK_SET_CODE_RE.search(text) or "double pack" in text or "doble pack" in text:
        pkg = _detect_packaging("DOUBLE_PACK", text)
        dp_code, main_set = _extract_dp_code_and_main_set(name_variant, text)
        return Classification("DOUBLE_PACK", dp_code, language, main_set, packaging=pkg)

    if _PRB_CODE_RE.search(text) or _lookup_code(_normalize_for_lookup(name_variant), _PRB_TITLE_CODES):
        pkg = _detect_packaging("PREMIUM_BOOSTER_BOX", text)
        return Classification("PREMIUM_BOOSTER_BOX", _extract_prb_code(name_variant), language, None, packaging=pkg)

    if _OP_CODE_RE.search(text) or _lookup_code(_normalize_for_lookup(name_variant), _OP_TITLE_CODES):
        pkg = _detect_packaging("ONE_PIECE", text)
        main_set = _extract_op_code(name_variant)
        return Classification("ONE_PIECE", main_set, language, main_set, packaging=pkg)

    if (
        _EB_CODE_RE.search(text)
        or "extra booster" in text
        or _lookup_code(_normalize_for_lookup(name_variant), _EB_TITLE_CODES)
    ):
        pkg = _detect_packaging("EXTRA_BOOSTER", text)
        return Classification("EXTRA_BOOSTER", _extract_eb_code(name_variant), language, None, packaging=pkg)

    if _GENERIC_SEALED_CATCHALL_RE.search(text) and not _ACCESORIO_AJENO_RE.search(text):
        # Catch-all de última instancia (decisión de producto 2026-08-30):
        # sin código ni título reconocible de ninguna família anterior, pero
        # con keyword de producto sellado -- se asume ONE_PIECE, el booster
        # principal, el que de verdad vende cualquier tienda sin
        # especificar más. set_code/main_set quedan None -- no hay ninguna
        # señal de QUÉ lanzamiento es, solo de que es un booster sellado.
        # Accesorio ajeno real (marca de terceros, caja de almacenaje
        # genérica) -> se excluye ANTES de asumir ONE_PIECE, cae a OTROS.
        pkg = _detect_packaging("ONE_PIECE", text)
        return Classification("ONE_PIECE", None, language, None, packaging=pkg)

    # Código de acompañamiento también para el catch-all genérico -- mismo
    # principio que LOTE_CARTAS/PROMO_CARD arriba: información barata de
    # conservar aunque nunca se use para comparar precio (OTROS es
    # not_applicable en matcher.py).
    return Classification("OTROS", _extract_generic_code(name_variant), language, None)


def classify_product(
    name: Optional[str], variant_title: Optional[str] = None, extra_type_hint: Optional[str] = None
) -> Classification:
    """Punto de entrada público. Clasifica un producto por tipo/set/idioma a
    partir de su nombre y, opcionalmente, del título de su variante y de
    `extra_type_hint` (en la práctica, `store_product.raw_tags` -- señal
    estructurada del comerciante, ej. `tags` nativo de Shopify).

    name+variant PRIMERO, sin tags; las tags solo entran como respaldo si
    eso da OTROS -- nunca pueden pisar una clasificación que name+variant ya
    resuelven por sí solos (las tags son metadato de catálogo del comercio,
    a menudo reciclado entre productos sin relación real)."""
    if not name:
        return Classification("OTROS", None, None, None)

    sin_tags = _classify_pass(name, variant_title, tags=None)
    if sin_tags.product_type != "OTROS" or not extra_type_hint:
        return sin_tags

    return _classify_pass(name, variant_title, tags=extra_type_hint)


def classify_with_category(
    name: Optional[str], variant_title: Optional[str] = None, extra_type_hint: Optional[str] = None
) -> tuple[Classification, Optional[str]]:
    """Combina classify_product() con el mapeo a category.slug en una única
    llamada -- evita que cada llamador (matcher._evaluate(),
    api/services/matches.py) reimplemente `PRODUCT_TYPE_TO_CATEGORY_SLUG.get(...)`
    a mano.

    Devuelve (classification, None) si el product_type no tiene categoría
    sembrada -- incluye tanto NOT_APPLICABLE_PRODUCT_TYPES como cualquier
    product_type reconocido pero aún sin categoría en el seed."""
    classification = classify_product(name, variant_title, extra_type_hint)
    category_slug = PRODUCT_TYPE_TO_CATEGORY_SLUG.get(classification.product_type)
    return classification, category_slug


# Mapea Classification.product_type a category.slug -- los 10 tipos
# matcheables reales, sembrados por seed-catalog-app-tcg.sql.
# LOTE_CARTAS/PROMO_CARD/MYSTERY_PACK/DICE_ACCESSORY/OTROS quedan fuera a
# propósito, ver NOT_APPLICABLE_PRODUCT_TYPES.
PRODUCT_TYPE_TO_CATEGORY_SLUG = {
    "ONE_PIECE": "one-piece",
    "EXTRA_BOOSTER": "extra-booster",
    "PREMIUM_BOOSTER_BOX": "premium-booster-box",
    "PREMIUM_CARD_COLLECTION": "premium-card-collection",
    "STARTER_DECK": "starter-deck",
    "ILLUSTRATION_BOX": "illustration-box",
    "DOUBLE_PACK": "double-pack",
    "DEVIL_FRUITS_COLLECTION": "devil-fruits-collection",
    "SLEEVES": "sleeves",
    "PLAYMAT": "playmat",
}

# LOTE_CARTAS/PROMO_CARD/MYSTERY_PACK/DICE_ACCESSORY (Fase 0, unidades/lotes
# individuales o accesorios sin producto sellado canónico equivalente) y
# OTROS (nada reconocido) -- ninguno tiene un canónico razonable con el que
# comparar precio, matcher._evaluate() los marca not_applicable sin gastar
# consulta de similitud.
NOT_APPLICABLE_PRODUCT_TYPES = {"LOTE_CARTAS", "PROMO_CARD", "MYSTERY_PACK", "DICE_ACCESSORY", "OTROS"}


# ---------------------------------------------------------------------------
# Detector de packaging genérico (Fase 2) -- display/case/sobre, calculado
# dentro de la família que lo necesita. "Sobre" es el default explícito si
# nada más matchea.
# ---------------------------------------------------------------------------

# "caja"/"box" (no solo "display" literal) -- la mayoría de listados reales
# de una caja completa dicen "Booster Box"/"Caja de sobres", nunca la
# palabra "Display" (esa keyword sola dejaba sin detectar el caso más común,
# encontrado al verificar la implementación contra nombres reales tipo
# "One Piece OP-16 Booster Box").
_DISPLAY_RE = re.compile(r"\bdisplay\b|\bcajas?\b|\bbox(?:es)?\b", re.IGNORECASE)
_CASE_KEYWORD_RE = re.compile(r"\bcase\b", re.IGNORECASE)
_QTY_RE = re.compile(r"\bx\s*(\d+)\b", re.IGNORECASE)

# Unidades reales por família+packaging -- fuente única reutilizada tanto
# por _detect_packaging() como por cantidad_es_ambigua() (antes había dos
# tablas separadas, una por category_slug para BOOSTER_BOX/PREMIUM_COLLECTION
# y otra de "categorías de unidad única"; con packaging como campo dentro de
# una única categoría, ONE_PIECE/EXTRA_BOOSTER/PREMIUM_BOOSTER_BOX conviven
# ahora con 3 cantidades válidas distintas en la MISMA família, así que la
# cantidad esperada depende de (product_type, packaging), no solo del
# primero).
_PACKAGING_UNITS = {
    "STARTER_DECK": {"display": 6},
    "DOUBLE_PACK": {"display": 10},
    "PREMIUM_BOOSTER_BOX": {"display": 20, "case": 10},
    "ONE_PIECE": {"display": 24, "case": 12},
    "EXTRA_BOOSTER": {"display": 24, "case": 12},
}


def _qty_matches(text: str, expected: Optional[int]) -> bool:
    if expected is None:
        return False
    m = _QTY_RE.search(text)
    return bool(m) and int(m.group(1)) == expected


def _detect_packaging(family: str, text: str) -> str:
    """"Sobre" es el default si nada más matchea -- no hace falta una regex
    para detectarlo explícitamente. "case" se comprueba ANTES que "display"
    a propósito -- un Booster Case real casi siempre menciona también
    "caja(s)"/"box" en su propia descripción (ej. "Case OP02 Paramount War
    (12 cajas)", "Booster Box Case ST-05"), así que si "case" está presente
    debe ganar sobre el "caja"/"box" decorativo."""
    units = _PACKAGING_UNITS.get(family, {})
    if _CASE_KEYWORD_RE.search(text) or _qty_matches(text, units.get("case")):
        return "case"
    if _DISPLAY_RE.search(text) or _qty_matches(text, units.get("display")):
        return "display"
    return "sobre"


# Catch-all de última instancia (decisión de producto 2026-08-30, cierra la
# pregunta de qué hacer con un producto sellado sin código ni título
# reconocible) -- mismas keywords atómicas que el sistema anterior usaba
# para sus categorías genéricas ("booster", "caja", "sobre "), unificadas en
# una sola regex con límites de palabra. Deliberadamente SIN "box" suelto --
# el sistema anterior solo reconocía "booster box" como FRASE (nunca "box"
# a secas) precisamente porque "box" solo colisiona con accesorios reales
# del catálogo oficial ("Official Storage Box", "Official Storage Box EX")
# que no son ningún booster sellado; "booster" ya cubre "booster box" al
# ser substring, sin arrastrar ese falso positivo. Se evalúa DESPUÉS de las
# diez família anteriores en _classify_pass -- para cuando se llega aquí,
# ya se descartó que el texto pertenezca a Starter Deck/Playmat/Sleeves/
# Double Pack/etc, así que "caja"/"booster"/"sobre" sueltos ya no pueden
# colisionar con esas família.
_GENERIC_SEALED_CATCHALL_RE = re.compile(r"\bbooster\b|\bcaja\b|\bsobres?\b", re.IGNORECASE)

# Accesorios ajenos reales encontrados en needs_review.csv (2026-08-30):
# "caja" a secas SÍ cuela marcas de accesorios de terceros (fundas/deck
# boxes, nunca un producto de Bandai) y cajas de almacenaje genéricas -- a
# diferencia de "box" (excluido arriba como palabra suelta), "caja" no se
# puede excluir sin más porque también es como se nombra la caja de sobres
# real en español. Se comprueba aquí, no como palabra prohibida genérica,
# para no arriesgar excluir un booster real que mencione alguna de estas
# palabras por casualidad en otro contexto.
_ACCESORIO_AJENO_RE = re.compile(
    r"ultimate guard|comic mania|caja de almacenamiento|storage box", re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Fase 2 -- Funciones de extracción de código por família (_extract_*_code).
# Cada família tiene su PROPIA regex/tabla -- nunca la de otra família
# (tablas exclusivas por línea: una tabla única compartida entre OP/PRB/EB
# permitía que, por ejemplo, "Eb-02 Sobre" con código explícito EB-02
# cayera igualmente en el lookup de título de OTRA família si esa tabla
# tenía una entrada que también matcheaba el texto).
#
# `(?!-\d)` en las seis regex de família de abajo: sin esto, un código de
# família aparece también como prefijo de la numeración de una CARTA
# INDIVIDUAL dentro de otro producto (ej. "CHOPPER's Vol.1 carta/comic
# promocional EB02-003" activaría EXTRA_BOOSTER por error). `_GENERIC_CODE_RE`
# (arriba, para Fase 0 y el catch-all OTROS) NO lleva este lookahead a
# propósito -- necesita seguir capturando el set de acompañamiento de una
# carta suelta ("OP10-058" -> "OP10"), justo el caso que aquí se excluye.
#
# Guarda de rango (`_RANGO_CODIGOS_RE`) al principio de cada función: un
# rango de códigos ("ST-15 - ST-20") no tiene código propio que inventar --
# la família igual se activa (por el código/keyword presente), solo el
# código de set queda en None.
# ---------------------------------------------------------------------------

_DF_CODE_RE = re.compile(r"\bDF[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)
_IB_CODE_RE = re.compile(r"\bIB[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)
_ST_CODE_RE = re.compile(r"\bST[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)  # espacio o guion (Arte9: "ST 36")
_PRB_CODE_RE = re.compile(r"\bPRB[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)
_OP_CODE_RE = re.compile(r"\bOP[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)  # espacio o guion (mismo criterio que ST, "OP 12")
_EB_CODE_RE = re.compile(r"\bEB[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)
_DOUBLE_PACK_SET_CODE_RE = re.compile(r"\bDP[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)

# Código explícito de Learn Together Deck Set ("LD01"/"LD-01") -- se respeta
# si aparece en el texto, con fallback fijo a "LD01" (único release real,
# ver _classify_pass) cuando no aparece.
_LEARN_TOGETHER_CODE_RE = re.compile(r"\bLD[\s-]?0*(\d{1,2})\b", re.IGNORECASE)

_VOLUME_RE = re.compile(r"\bvol\.?\s*0*(\d{1,3})\b", re.IGNORECASE)

# Fallback cuando el código no viene abreviado ("Double Pack Set 10" / con
# "Vol." en vez de "DP-10").
_DOUBLE_PACK_SET_NUM_RE = re.compile(r"\bdouble pack set\s*0*(\d{1,3})\b", re.IGNORECASE)
_DOUBLE_PACK_VOL_NUM_RE = re.compile(r"\bdouble pack.*?vol\.?\s*0*(\d{1,3})\b", re.IGNORECASE)

# Número suelto tras "Starter Deck", sin ninguna letra "ST" en el texto
# (Gameria: "Starter Deck 23"/"Uta Starter Deck 16").
_STARTER_DECK_NUM_RE = re.compile(r"\bstarter deck\s*0*(\d{1,2})\b", re.IGNORECASE)

# Mismo patrón que el de arriba pero en español -- "mazo" ya es la keyword
# que activa la família Starter Deck (ver _classify_pass), "One Piece" en
# medio es opcional porque el caso real visto es "MAZO ONE PIECE NN" pero no
# hay motivo para exigirlo si algún día aparece sin él.
_STARTER_DECK_NUM_ES_RE = re.compile(r"\bmazo\b(?:\s+one\s+piece)?\s*0*(\d{1,2})\b", re.IGNORECASE)

# Número suelto tras "sleeve(s)" -- la línea numerada oficial ("Official
# Sleeves 11"/"Official Card Sleeves 14") NUNCA lleva la palabra "Vol.", a
# diferencia de las otras líneas de fundas (Limited Edition/TCG+ Store
# Edition/Premium Matte, esas sí "Vol.N"). Se comprueba solo como fallback
# de _extract_volume_code, así que un nombre con "Vol." real (de esas otras
# líneas) nunca llega aquí.
_SLEEVES_NUM_RE = re.compile(r"\bsleeves?\s*0*(\d{1,2})\b", re.IGNORECASE)

# Abreviatura "v.NN"/"v NN" de "Vol.NN" vista en tienda para esta misma
# línea (p.ej. "Fundas v.16"). SOLO dentro de SLEEVES, nunca en _VOLUME_RE
# compartido con las demás famílias (DF/Illustration Box/Playmat/Premium
# Card Collection) -- "v." a secas es demasiado genérico fuera de un texto
# que ya sabemos que es de fundas (chocaría con "v.2024"/versión de
# software/etc. en esas otras famílias). \d{1,2} + \b de cierre ya excluye
# años de 4 dígitos ("v.2024" no hace match completo).
_SLEEVES_V_RE = re.compile(r"\bv\.?\s*0*(\d{1,2})\b", re.IGNORECASE)


def _extract_volume_code(name: str, prefix: str) -> Optional[str]:
    m = _VOLUME_RE.search(name)
    return f"{prefix}{int(m.group(1)):02d}" if m else None


def _extract_sleeves_code(name: str) -> Optional[str]:
    # `name` aquí ya es name_variant (name+variant_title combinados, ver
    # _classify_pass) -- Sleeves lo necesita en particular porque tiendas
    # Shopify reales meten el "Vol.NN" en variant_title, no en el nombre
    # principal (ej. name="Official Sleeves", variant="Card Sleeves Monkey
    # D. Luffy Vol.01 ...").
    code = _extract_volume_code(name, "VOL")
    if code is None:
        m = _SLEEVES_NUM_RE.search(name)
        code = f"VOL{int(m.group(1)):02d}" if m else None
    if code is None:
        m = _SLEEVES_V_RE.search(name)
        code = f"VOL{int(m.group(1)):02d}" if m else None
    if code is None:
        code = _lookup_code(_normalize_for_lookup(name), _SLEEVES_VARIANT_CODES)
    return code


def _extract_df_code(name: str) -> Optional[str]:
    if _RANGO_CODIGOS_RE.search(name):
        return None
    m = _DF_CODE_RE.search(name)
    if m:
        return f"DF{int(m.group(1)):02d}"
    # Fallback Vol.N -> DFNN -- verificado que Vol.N y el código DF de esta
    # família son el mismo número (ej. "Vol.3 Op-Op Fruit (DF03)").
    return _extract_volume_code(name, "DF")


def _extract_illustration_box_code(name: str) -> Optional[str]:
    code = _extract_volume_code(name, "VOL")
    if code is None:
        if _RANGO_CODIGOS_RE.search(name):
            return None
        # Convención alternativa IB-NN sin "Vol." en el texto (Distrito
        # Zero/Gameria), mismo VOL{NN} de salida.
        m = _IB_CODE_RE.search(name)
        code = f"VOL{int(m.group(1)):02d}" if m else None
    return code


# Personaje protagonista -> ST-code -- tiendas que nombran el Starter Deck
# solo por el personaje, sin color ni código. Combos de DOS personajes van
# primero (más específicos). Los 4 personajes que Bandai reutilizó en varios
# Starter Deck de color distinto (Monkey D. Luffy x4, Charlotte Katakuri x2,
# Yamato x2, Uta x2) se dejan FUERA a propósito -- sin la palabra de color no
# hay señal para desambiguar, mejor needs_review que un match falso.
_STARTER_DECK_CHARACTER_CODES: tuple[tuple[str, str], ...] = (
    ("ace newgate", "ST22"),
    ("ace and newgate", "ST22"),
    ("luffy ace", "ST30"),
    ("luffy and ace", "ST30"),
    ("zoro and sanji", "ST12"),
    ("edward newgate", "ST15"),
    ("edward.newgate", "ST15"),
    ("straw hat crew", "ST01"),
    ("worst generation", "ST02"),
    ("seven warlords", "ST03"),
    ("animal kingdom pirates", "ST04"),
    ("absolute justice", "ST06"),
    ("big mom pirates", "ST07"),
    ("three captains", "ST10"),
    ("three brothers", "ST13"),
    ("3d2y", "ST14"),
    ("donquixote doflamingo", "ST17"),
    ("gear 5", "ST21"),
    ("gear5", "ST21"),
    ("jewelry bonney", "ST24"),
    ("marshall d.teach", "ST27"),
    ("marshall d teach", "ST27"),
    ("marshall.d.teach", "ST27"),
    ("roronoa zoro", "ST32"),
    ("eustass", "ST36"),
    ("captain kid", "ST36"),
    ("smoker", "ST19"),
    ("shanks", "ST23"),
    ("buggy", "ST25"),
    ("egghead", "ST29"),
    ("kuzan", "ST33"),
    ("sabo", "ST35"),
)

# Título completo del Starter Deck -> código, construida contra las 33
# entradas ST reales del catálogo oficial (excluidas variantes "PRE").
# Deliberadamente fuera: ST-08/09/11 ("Monkey D. Luffy"/"Yamato"/"Uta" a
# secas) -- personajes reutilizados sin cualificador de color, siguen siendo
# ambiguos igual que en _STARTER_DECK_CHARACTER_CODES.
_ST_TITLE_CODES: tuple[tuple[str, str], ...] = (
    ("straw hat crew", "ST01"),
    ("worst generation", "ST02"),
    ("the seven warlords of the sea", "ST03"),
    ("animal kingdom pirates", "ST04"),
    ("one piece film edition", "ST05"),
    ("absolute justice", "ST06"),
    ("big mom pirates", "ST07"),
    ("the three captains", "ST10"),
    ("zoro and sanji", "ST12"),
    ("the three brothers", "ST13"),
    ("3d2y", "ST14"),
    ("red edward newgate", "ST15"),
    ("green uta", "ST16"),
    ("blue donquixote doflamingo", "ST17"),
    ("purple monkey d luffy", "ST18"),
    ("black smoker", "ST19"),
    ("yellow charlotte katakuri", "ST20"),
    ("gear 5", "ST21"),
    ("ace newgate", "ST22"),
    ("red shanks", "ST23"),
    ("green jewelry bonney", "ST24"),
    ("blue buggy", "ST25"),
    ("purple black monkey d luffy", "ST26"),
    ("black marshall d teach", "ST27"),
    ("green yellow yamato", "ST28"),
    ("egghead", "ST29"),
    ("luffy ace", "ST30"),
    ("red monkey d luffy", "ST31"),
    ("green roronoa zoro", "ST32"),
    ("blue kuzan", "ST33"),
    ("purple charlotte katakuri", "ST34"),
    ("red black sabo", "ST35"),
    ("yellow eustass captain kid", "ST36"),
)


def _extract_st_code(name: str) -> Optional[str]:
    if _RANGO_CODIGOS_RE.search(name):
        return None
    m = _ST_CODE_RE.search(name)
    if m:
        return f"ST{int(m.group(1)):02d}"
    m = _STARTER_DECK_NUM_RE.search(name.lower())
    if m:
        return f"ST{int(m.group(1)):02d}"
    m = _STARTER_DECK_NUM_ES_RE.search(name.lower())
    if m:
        return f"ST{int(m.group(1)):02d}"
    return _lookup_code(_normalize_for_lookup(name), _ST_TITLE_CODES)


# Tabla DP<->OP: 1 Double Pack por 1 booster OP, misma fecha de lanzamiento
# (verificado contra el catálogo oficial, data/one_piece_tcg_products.json:
# release_date de "Double Pack Set Vol.N" == release_date de su "Booster
# Pack" correspondiente). Sin red, sin BBDD -- hardcodeada a propósito,
# mismo principio que el resto de este módulo. DP09 corregido 2026-08-30 --
# tenía OP13 ("Carrying on His Will"), pero el catálogo oficial confirma que
# comparte fecha de lanzamiento (2026-01-16) con OP14 ("The Azure Sea's
# Seven"), no con OP13 (que no tiene Double Pack asociado, igual que OP01-03
# y OP10 -- Bandai no sacó DP para todos los lanzamientos).
_DP_TO_OP: dict[str, str] = {
    "DP01": "OP04",
    "DP02": "OP05",
    "DP03": "OP06",
    "DP04": "OP07",
    "DP05": "OP08",
    "DP06": "OP09",
    "DP07": "OP11",
    "DP08": "OP12",
    "DP09": "OP14",
    "DP10": "OP15",
    "DP11": "OP16",
    "DP12": "OP17",
}


def _extract_dp_code_and_main_set(name: str, text: str) -> tuple[Optional[str], Optional[str]]:
    """Orden: código explícito DP-NN primero; si no está, número escrito
    ("Set N" / "Vol N" / "Vol.N"); por último, título oficial del booster
    asociado (_DP_TITLE_CODES -- p.ej. "The World's Strongest Warriors
    Double Pack" sin ningún código visible). Una vez ubicado el DP-NN por
    cualquiera de las tres vías, main_set se deriva de _DP_TO_OP -- nunca al
    revés (un OP decorativo en el texto no es el identificador real del
    Double Pack). Guarda de rango al principio -- un "DP-NN - DP-MM" no
    tiene código propio, y sin dp_code main_set sale None solo."""
    if _RANGO_CODIGOS_RE.search(name):
        return None, None
    m = _DOUBLE_PACK_SET_CODE_RE.search(name)
    dp_code = f"DP{int(m.group(1)):02d}" if m else None
    if dp_code is None:
        m = _DOUBLE_PACK_SET_NUM_RE.search(text) or _DOUBLE_PACK_VOL_NUM_RE.search(text)
        dp_code = f"DP{int(m.group(1)):02d}" if m else None
    if dp_code is None:
        dp_code = _lookup_code(_normalize_for_lookup(name), _DP_TITLE_CODES)
    main_set = _DP_TO_OP.get(dp_code) if dp_code else None
    return dp_code, main_set


def _extract_prb_code(name: str) -> Optional[str]:
    if _RANGO_CODIGOS_RE.search(name):
        return None
    m = _PRB_CODE_RE.search(name)
    if m:
        return f"PRB{int(m.group(1)):02d}"
    return _lookup_code(_normalize_for_lookup(name), _PRB_TITLE_CODES)


def _extract_op_code(name: str) -> Optional[str]:
    if _RANGO_CODIGOS_RE.search(name):
        return None
    m = _OP_CODE_RE.search(name)
    if m:
        return f"OP{int(m.group(1)):02d}"
    return _lookup_code(_normalize_for_lookup(name), _OP_TITLE_CODES)


def _extract_eb_code(name: str) -> Optional[str]:
    if _RANGO_CODIGOS_RE.search(name):
        return None
    m = _EB_CODE_RE.search(name)
    if m:
        return f"EB{int(m.group(1)):02d}"
    return _lookup_code(_normalize_for_lookup(name), _EB_TITLE_CODES)


# Título oficial de release (Bandai) -> código -- tablas EXCLUSIVAS por
# línea (ver comentario de arriba sobre por qué no puede ser una tabla
# única compartida). Variantes de PRB (con/sin "2"/"vol.2") van ANTES de la
# entrada genérica "the best" -> PRB01. Incluye typos/variantes reales
# vistas en tiendas junto a la grafía oficial.
_OP_TITLE_CODES: tuple[tuple[str, str], ...] = (
    ("romance dawn", "OP01"),
    ("paramount war", "OP02"),
    ("pillars of strength", "OP03"),
    ("pillard of strength", "OP03"),
    ("kingdoms of intrigue", "OP04"),
    ("awakening of the new era", "OP05"),
    ("wings of the captain", "OP06"),
    ("500 years in the future", "OP07"),
    ("500 years into the future", "OP07"),
    ("two legends", "OP08"),
    ("emperors in the new world", "OP09"),
    ("royal blood", "OP10"),
    ("a fist of divine speed", "OP11"),
    ("a fist divine speed", "OP11"),
    ("legacy of the master", "OP12"),
    ("legacy of the masters", "OP12"),
    ("carrying on his will", "OP13"),
    ("the azure seas seven", "OP14"),
    ("adventure on kamis island", "OP15"),
    ("the time of battle", "OP16"),
    ("the worlds strongest warriors", "OP17"),
)

# Título del BOOSTER asociado -> código DP -- derivada de _OP_TITLE_CODES +
# _DP_TO_OP (nunca escrita a mano) para que no se pueda desincronizar de
# ellas: un Double Pack real casi siempre se anuncia por el título del
# booster con el que comparte lanzamiento ("The World's Strongest Warriors
# Double Pack"), sin código DP-NN visible en el texto. Los títulos de OP01-
# 03/OP10/OP13 no aparecen aquí porque _DP_TO_OP no tiene ningún DP asociado
# a esos (Bandai no sacó Double Pack para todos los booster).
_OP_TO_DP: dict[str, str] = {op: dp for dp, op in _DP_TO_OP.items()}
_DP_TITLE_CODES: tuple[tuple[str, str], ...] = tuple(
    (title, _OP_TO_DP[op_code]) for title, op_code in _OP_TITLE_CODES if op_code in _OP_TO_DP
)

_PRB_TITLE_CODES: tuple[tuple[str, str], ...] = (
    ("the best 2", "PRB02"),
    ("the best vol 2", "PRB02"),
    ("the best vol.2", "PRB02"),
    ("the best", "PRB01"),
)

_EB_TITLE_CODES: tuple[tuple[str, str], ...] = (
    ("heroines edition vol 2", "EB05"),
    ("heroines edition vol.2", "EB05"),
    ("heroines edition", "EB03"),
    ("memorial collection", "EB01"),
    ("anime 25th collection", "EB02"),
)

# Nombres de edición propios de Premium Card Collection -- família aparte,
# NUNCA la tabla de OP/PRB/EB (no es una "línea de expansión", es un
# producto distinto). Ninguna de las 17 entradas reales del catálogo tiene
# código asignado por Bandai -- identificador inventado a propósito, mismo
# criterio que Playmat/Starter Deck. Se comprueba Vol.N primero (identificador
# real cuando existe), esta tabla solo como fallback.
_PREMIUM_CARD_COLLECTION_EDITION_CODES: tuple[tuple[str, str], ...] = (
    ("25th edition", "ED25TH"),
    ("film red edition", "EDFILMRED"),
    ("best selection", "EDBEST"),
    ("live action edition", "EDLIVEACTION"),
    ("bandai card games fest", "EDFEST2324"),
    ("leader collection", "EDLEADER"),
    ("29th anniversary edition", "ED29TH"),
    ("ace sabo luffy", "EDACESABOLUFFY"),
)

# Fallback SOLO si la tabla estricta de arriba no dio nada (needs_review.csv,
# 2026-08-30): varias tiendas reales omiten la palabra genérica final de la
# frase oficial -- "Fest. 23-24 Edition" en vez de "Bandai Card Games Fest
# 23-24 Edition", "LIVE ACTION" a secas en vez de "Live Action Edition",
# "-25th-" en vez de "25th Edition". Se comprueba en segundo lugar a
# propósito -- si la frase completa está, esa gana; esta tabla es más
# corta y en teoría más propensa a colisionar si Bandai reutiliza alguna
# vez estas palabras sueltas en una edición distinta.
_PREMIUM_CARD_COLLECTION_EDITION_CODES_SHORT: tuple[tuple[str, str], ...] = (
    ("25th", "ED25TH"),
    ("live action", "EDLIVEACTION"),
    ("fest", "EDFEST2324"),
    ("29th anniversary", "ED29TH"),
)

# Personaje -> pseudo-código de playmat -- NO son códigos reales de Bandai
# (los playmats con personaje nunca tienen código), identificadores
# inventados a propósito para desambiguar dentro de la categoría vía el
# mismo mecanismo set_code que usa el resto del matcher. Ningún personaje se
# repite entre playmats (a diferencia de Starter Deck).
_PLAYMAT_CHARACTER_CODES: tuple[tuple[str, str], ...] = (
    ("trafalgar law", "LAW"),
    ("trafalgar.law", "LAW"),
    ("portgas d ace", "ACE"),
    ("portgas.d.ace", "ACE"),
    ("porgas d ace", "ACE"),
    ("eustass", "KID"),
    ("captain kid", "KID"),
    ("shanks", "SHANKS"),
    ("nami", "NAMI"),
)

# Personaje/tema -> VOLNN de la línea numerada "Official Sleeves 1-16" --
# mismo namespace de código que ya produce _extract_volume_code/_SLEEVES_NUM_RE
# para esa misma línea (un match por nombre y un match por "Vol./número"
# caen en el mismo set_code, sin duplicar canónicos). Fuera a propósito los
# personajes que Bandai reutilizó en 2+ volúmenes de esta línea (Monkey D.
# Luffy salvo "Gear 5", Eustass"Captain"Kid, Tony Tony Chopper, Yamato, Nami,
# Devil Fruits, Enel, Roronoa Zoro, Boa Hancock) y los nombres de color
# "Standard Blue"/"Standard Purple" a secas -- estos últimos, a diferencia
# del resto de "Standard X" de esta tabla, coinciden con el prefijo de
# ediciones especiales YA vistas en el catálogo oficial de otra línea de
# fundas distinta ("Limited Card Sleeve -Standard Blue Gold-"/"-Standard
# Purple Silver-"), así que un match por substring les asignaría el volumen
# equivocado -- mejor needs_review que un match falso. Los pares con
# colisión de substring (variante base vs. variante cualificada del mismo
# personaje en OTRO volumen) van con la más específica primero.
_SLEEVES_VARIANT_CODES: tuple[tuple[str, str], ...] = (
    ("the three captains pixel art", "VOL06"),
    ("the three captains", "VOL04"),
    ("standard blue ver rocks d xebec", "VOL16"),

    ("crocodile", "VOL01"),
    ("kaido", "VOL01"),

    ("standard pink", "VOL02"),

    ("charlotte katakuri", "VOL03"),
    ("navy", "VOL03"),
    ("uta", "VOL03"),

    ("vinsmoke reiju", "VOL05"),
    ("zoro sanji", "VOL05"),
    ("standard green", "VOL05"),

    ("trafalgar law", "VOL06"),
    ("perona", "VOL06"),
    ("standard black pink", "VOL06"),

    ("ulti", "VOL07"),
    ("edward newgate", "VOL07"),
    ("silvers rayleigh", "VOL07"),
    ("the three brothers", "VOL07"),

    ("gecko moria", "VOL08"),

    ("sogeking", "VOL09"),
    ("sugar", "VOL09"),
    ("standard mint lemon", "VOL09"),

    ("kouzuki hiyori", "VOL10"),
    ("portgas d ace", "VOL10"),
    ("shanks", "VOL10"),

    ("kuzan", "VOL11"),
    ("donquixote rosinante", "VOL11"),

    ("monkey d luffy gear 5", "VOL12"),
    ("sabo", "VOL12"),
    ("gol d roger", "VOL12"),
    ("imu", "VOL12"),

    ("dracule mihawk", "VOL13"),
    ("hat patterns", "VOL13"),

    ("jewelry bonney", "VOL14"),
    ("brook laboon", "VOL14"),

    ("luffy ace", "VOL15"),
    ("impel down", "VOL15"),
    ("the three admirals", "VOL15"),
)


# ---------------------------------------------------------------------------
# cantidad_es_ambigua -- consciente de (product_type, packaging), no solo de
# category_slug. Con packaging como campo dentro de una única categoría,
# ONE_PIECE/EXTRA_BOOSTER/PREMIUM_BOOSTER_BOX conviven con varias cantidades
# válidas distintas en la MISMA família -- la cantidad esperada depende de
# las dos cosas.
# ---------------------------------------------------------------------------

_CANTIDAD_SOSPECHOSA_RE = re.compile(r"\bpack\s*(\d+)\b|\b(\d+)\s*sobres\b|\bx\s*(\d+)\b", re.IGNORECASE)


def cantidad_es_ambigua(raw_name: Optional[str], product_type: str, packaging: Optional[str]) -> bool:
    """True si raw_name menciona una cantidad que sugiere una unidad de venta
    distinta a la que corresponde a esta classification. Reutiliza
    _PACKAGING_UNITS (la misma tabla que _detect_packaging) como única
    fuente de la cantidad esperada. product_type sin entrada en la tabla
    (Playmat, Sleeves, Illustration Box, Devil Fruits Collection, Premium
    Card Collection...) -> família de unidad única, cualquier cantidad >1 es
    sospechosa de bundle real."""
    if not raw_name:
        return False
    match = _CANTIDAD_SOSPECHOSA_RE.search(raw_name)
    if not match:
        return False
    numero = int(next(g for g in match.groups() if g))
    units = _PACKAGING_UNITS.get(product_type)
    if units is None:
        return numero != 1
    expected = units.get(packaging)
    return numero != (expected if expected is not None else 1)


# ---------------------------------------------------------------------------
# Parseo de precios -- sin relación con la clasificación, sin cambios.
# ---------------------------------------------------------------------------


def parse_price_text(text) -> Optional[float]:
    """Parsea un precio en texto libre o numérico directo. Soporta formato
    español ('1.234,56') y anglosajón/decimal simple ('1234.56' o 1234.56)."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = re.sub(r"[^\d,.\-]", "", str(text))
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_price_minor_unit(raw, minor_unit: int = 2) -> Optional[float]:
    """Precio devuelto como entero en la unidad mínima (céntimos), típico de
    Store APIs modernas: '16000' con minor_unit=2 -> 160.00 (WooCommerce Store API)."""
    if raw is None:
        return None
    try:
        return round(int(raw) / (10**minor_unit), 2)
    except (TypeError, ValueError):
        return None

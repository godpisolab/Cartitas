"""Lógica de negocio pura: clasificación de producto y parseo de precios.
Sin red, sin BBDD, sin logging con estado -- solo funciones de texto sobre
datos ya en memoria, compartidas por el scraper, el matcher (bloque C), la
siembra del catálogo oficial (seed_official_catalog.py) y el panel de
matching de api/ (services/matches.py).

Vive en shared/ (no en store_monitor/) por el mismo motivo que domain.py --
ver decisión de arquitectura sobre el acoplamiento entre api/ y
store_monitor/ (patrón Shared Kernel de DDD).
"""

from __future__ import annotations

import re
from typing import Optional

from .domain import Classification

CLASSIFICATION_RULES = [
    # "ultra deck" añadido tras sembrar el catálogo oficial de Bandai (ST-10
    # "Ultra Deck: The Three Captains", ST-13 "...The Three Brothers"):
    # mismo tipo de producto que un Starter Deck (mazo único en caja), solo
    # cambia el nombre de línea -- no hay categoría "Ultra Deck" separada en
    # D.2, y no tendría sentido crear una por dos productos.
    # LEARN_DECK antes que STARTER_DECK: un raw_name real de Arte9 --
    # "LEARN TOGETHER DECK SET – STARTER DECKS ONE PIECE" -- contiene AMBAS
    # keywords ("learn together" Y "starter decks"). El orden de la lista
    # decide qué regla gana (primer match, ver el bucle de abajo) -- puesto
    # en el orden anterior, "starter deck" ganaba primero y el item acababa
    # buscando candidatos en la categoría equivocada (sin ninguno bueno).
    ("LEARN_DECK", ["learn together", "learn to play"]),
    ("STARTER_DECK", ["starter deck", "ultra deck", "mazo de inicio"]),
    ("DOUBLE_PACK", ["double pack"]),
    ("MYSTERY_PACK", ["mystery pack", "mystery box"]),
    ("PREMIUM_COLLECTION", ["premium card collection", "the best vol", "the best "]),
    ("ILLUSTRATION_BOX", ["illustration box", "caja de ilustraciones"]),
    ("DEVIL_FRUITS_COLLECTION", ["devil fruits collection"]),
    ("PLAYMAT", ["playmat", "tapete"]),
    ("PROMO_CARD", ["carta promo", "promo pack", "promotion pack"]),
    ("DICE_ACCESSORY", ["dice"]),
    ("LOTE_CARTAS", ["lote"]),
    ("BOOSTER_BOX", ["booster box", "caja de sobres", "caja one piece", "caja"]),
    ("BOOSTER_PACK", ["booster", "sobre "]),
]

# Mapea Classification.product_type (CLASSIFICATION_RULES de arriba) a
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

# Prefijos de código de set REALMENTE usados en el catálogo (verificado
# contra product.set_code, 2026-08-27) -- una lista blanca explícita, no
# "cualquier 2-3 mayúsculas seguidas de dígitos". Encontrado el motivo
# revisando el impacto real del matcher: un patrón genérico capturaba
# palabras incidentales en mayúsculas que no son ningún código de set (ej.
# "VOL2" en "THE BEST VOL2 - PRB02", cogido ANTES de llegar al código real
# más adelante en el texto) -- eso hacía que matcher.py comparase un
# set_code inventado contra el candidato correcto y lo rechazara por
# "distinto set", un falso negativo. Añadir un prefijo nuevo (una línea
# aquí) es la única forma soportada de ampliar esto -- nunca aflojar el
# patrón a algo genérico otra vez.
_SET_CODE_PREFIXES = ("OP", "ST", "DP", "EB", "PRB", "DF")

# Familias cuyo identificador real es el número de volumen ("Vol.N"), no un
# código de letras -- verificado en product.set_code (2026-08-27): Playmat
# e Illustration Box nunca tienen ninguno de los prefijos de arriba. Sus
# raw_names a veces mencionan el set OP que acompaña al producto como
# CONTEXTO decorativo ("Illustration Box Vol.6 ... OP13", "Playmat ...
# Vol.1 + 3 Sobres OP-15") -- buscar un prefijo ahí capturaba ese OP-set
# decorativo en vez del volumen real, que es el identificador que de
# verdad distingue un producto de otro dentro de la familia.
_VOLUME_IDENTIFIED_PRODUCT_TYPES = {"ILLUSTRATION_BOX", "PLAYMAT"}
_VOLUME_RE = re.compile(r"\bvol\.?\s*0*(\d{1,3})\b", re.IGNORECASE)


def _detect_language(text: Optional[str]) -> Optional[str]:
    """Detecta idioma en un fragmento de texto (nombre de producto o título de
    variante). El chequeo de "EN" es case-sensitive a propósito (sobre el texto
    ORIGINAL, no en minúsculas) para no confundir con la preposición española
    "en", que aparece constantemente en descripciones de productos."""
    if not text:
        return None
    lower = text.lower()
    if "japones" in lower or "japonés" in lower:
        return "JP"
    if "coreano" in lower:
        return "KR"
    if "ingles" in lower or "inglés" in lower or re.search(r"\bEN\b", text) or "- en" in lower:
        return "EN"
    if "castellano" in lower or "español" in lower:
        return "ES"
    return None


def classify_product(name: Optional[str], variant_title: Optional[str] = None) -> Classification:
    """Clasifica un producto por tipo/set/idioma a partir de su nombre y,
    opcionalmente, del título de su variante (usado como fallback de idioma
    cuando el nombre compartido del producto no lo delata -- p.ej. cuando
    "Inglés"/"Japonés" es una VARIANTE y no parte del título común, como pasa
    en Pokemillon: dos filas del mismo producto, una por variante, cada una
    con su propio stock)."""
    if not name:
        return Classification("OTROS", None, None, None)

    name_lower = name.lower()

    product_type = "OTROS"
    for tipo, keywords in CLASSIFICATION_RULES:
        if any(kw in name_lower for kw in keywords):
            product_type = tipo
            break

    if product_type in _VOLUME_IDENTIFIED_PRODUCT_TYPES:
        # Ver _VOLUME_IDENTIFIED_PRODUCT_TYPES -- Vol.N, no un prefijo de
        # catálogo (que aquí sería el OP-set decorativo de acompañamiento).
        vol_match = _VOLUME_RE.search(name)
        set_code = f"VOL{int(vol_match.group(1)):02d}" if vol_match else None
    else:
        # [\s-]? (no solo guion) -- verificado en Arte9: su convención de
        # nombres separa letra y número con un espacio ("ST 36"), no un
        # guion ("ST-36") ni nada ("ST36") como el resto del catálogo. Sin
        # esto, set_code salía None para esos raw_names y el matcher
        # (matcher.py) no tenía con qué comparar el código de set de esa
        # tienda.
        #
        # Solo los prefijos de _SET_CODE_PREFIXES (lista blanca real, no
        # "cualquier 2-3 mayúsculas") -- ver el comentario de esa constante.
        set_match = re.search(rf"\b({'|'.join(_SET_CODE_PREFIXES)})[\s-]?(\d{{1,3}})\b", name)
        set_code = f"{set_match.group(1)}{set_match.group(2)}" if set_match else None

    main_set_match = re.search(r"\bOP[\s-]?0*(\d{1,2})\b", name, re.IGNORECASE)
    main_set = f"OP{int(main_set_match.group(1)):02d}" if main_set_match else None

    language = _detect_language(name)
    if language is None:
        language = _detect_language(variant_title)

    return Classification(product_type, set_code, language, main_set)


def classify_with_category(name: Optional[str], variant_title: Optional[str] = None) -> tuple[Classification, Optional[str]]:
    """Combina classify_product() con el mapeo a category.slug en una única
    llamada. Existe porque `classify_product(...)` seguido de
    `PRODUCT_TYPE_TO_CATEGORY_SLUG.get(classification.product_type)` se
    encontró duplicado, idéntico, en matcher._evaluate() y en
    api/services/matches.py::_candidates_for() -- cualquier llamador que
    tenga un raw_name/raw_variant y necesite la categoría debería usar esta
    función en vez de rehacer la combinación a mano.

    Devuelve (classification, None) si el product_type no tiene categoría
    sembrada -- incluye tanto NOT_APPLICABLE_PRODUCT_TYPES (LOTE_CARTAS,
    OTROS, fuera de PRODUCT_TYPE_TO_CATEGORY_SLUG a propósito) como
    cualquier product_type reconocido pero aún sin categoría en D.2."""
    classification = classify_product(name, variant_title)
    category_slug = PRODUCT_TYPE_TO_CATEGORY_SLUG.get(classification.product_type)
    return classification, category_slug


def is_box_variant(text: Optional[str]) -> Optional[bool]:
    """True si el texto indica claramente la variante CAJA/BOX (varios
    sobres/cartas del mismo lanzamiento), False si indica SOBRE/PACK (uno
    solo), None si no hay señal clara.

    Para BOOSTER_BOX/BOOSTER_PACK esto ya lo resuelve category_id (viven en
    categorías SEPARADAS, ver CLASSIFICATION_RULES) -- esta función existe
    para familias que NO separan caja/sobre por categoría propia. Caso real
    encontrado revisando la cola de matching (2026-08-27): "Premium
    Booster: ... PRB-02" (sobre) y "Premium Booster Box: ... PRB-02" (caja)
    conviven en la misma categoría `premium-collection` con el MISMO
    set_code -- ese desempate no distingue cuál es cuál, hace falta esta
    señal de texto adicional."""
    if not text:
        return None
    lower = text.lower()
    if "caja" in lower or "box" in lower:
        return True
    if "sobre" in lower:
        return False
    return None


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
        return round(int(raw) / (10 ** minor_unit), 2)
    except (TypeError, ValueError):
        return None

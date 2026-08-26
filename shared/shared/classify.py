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
    ("STARTER_DECK", ["starter deck", "ultra deck", "mazo de inicio"]),
    ("DOUBLE_PACK", ["double pack"]),
    ("MYSTERY_PACK", ["mystery pack", "mystery box"]),
    ("PREMIUM_COLLECTION", ["premium card collection", "the best vol", "the best "]),
    ("ILLUSTRATION_BOX", ["illustration box", "caja de ilustraciones"]),
    ("DEVIL_FRUITS_COLLECTION", ["devil fruits collection"]),
    ("LEARN_DECK", ["learn together", "learn to play"]),
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

    set_match = re.search(r"\b([A-Z]{2,3}-?\d{1,3})\b", name)
    set_code = set_match.group(1).replace("-", "") if set_match else None

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

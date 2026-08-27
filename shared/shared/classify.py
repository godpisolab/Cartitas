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
    # PRIMERO de toda la lista, a propósito (2026-08-27, investigación sobre
    # multi_tienda_one_piece.csv real): CGC/PSA/BGS son empresas de gradeo
    # de CARTAS INDIVIDUALES (confirmado por el propietario del proyecto) --
    # el precio depende del grado exacto, no hay dos unidades iguales, no
    # tiene sentido compararlo contra un canónico de producto sellado
    # (mismo motivo que LOTE_CARTAS). Tiene que ir ANTES que todo lo demás:
    # el grado vive en `variant` ("CGC 10 Pristine"), pero el `name` suele
    # mencionar el set/edición de origen de la carta ("...The Best",
    # "...Collection"...) -- si se comprobara después, con name+variant ya
    # combinados esas cartas colarían como PREMIUM_COLLECTION o similar por
    # el texto de acompañamiento, no por ser lo que de verdad son.
    ("LOTE_CARTAS", ["cgc", "psa", "bgs"]),
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
    # "mazo" a secas (2026-08-27, sin ", de inicio"): verificado contra el
    # CSV real que "mazo de inicio" dejaba fuera "Mazo ST-22 Ace &
    # Shirohige" y variantes similares -- la palabra completa "mazo" ya
    # implica Starter/Ultra Deck en este catálogo, no hace falta la frase
    # completa. Comprobado que no colisiona con "amazon" (que sí contiene
    # "mazo" como substring) en ninguna fila real de las 30 tiendas.
    ("STARTER_DECK", ["starter deck", "ultra deck", "mazo"]),
    # "doble pack" (2026-08-27): variante en español encontrada en el CSV
    # real ("ONE PIECE TCG - DOBLE PACK SET VOL. 14 (DP-14)") -- mismo
    # patrón que "mazo de inicio" para STARTER_DECK, alguna tienda nombra
    # el producto en español en vez de en inglés.
    ("DOUBLE_PACK", ["double pack", "doble pack"]),
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

# Fallback tras la lista de arriba, no dentro (2026-08-27, investigación
# sobre multi_tienda_one_piece.csv real): dos casos que un simple `in`
# sobre texto no puede resolver bien sin arriesgar un falso positivo nuevo.
#
# 1) "sobre " (con espacio final, en CLASSIFICATION_RULES) nunca matchea el
#    PLURAL "sobres" ("Pack 5 Sobres...") -- el espacio final se puso a
#    propósito para no colar "sobresaliente", así que añadir "sobres" sin
#    más como keyword suelta reintroduciría exactamente ese mismo problema
#    (substring de "sobresaliente"). \bsobres\b con límites de palabra
#    resuelve las dos cosas a la vez: coge el plural, sigue sin colar
#    "sobresaliente".
_BOOSTER_PACK_PLURAL_RE = re.compile(r"\bsobres\b")

# 2) Código DP-NN/EB-NN sin ninguna palabra de tipo alrededor -- señal más
#    débil que una keyword real, así que se aplica DESPUÉS de todo lo
#    demás, nunca antes. (?!-\d) al final excluye el código de una CARTA
#    INDIVIDUAL con numeración propia (ej. "EB02-003", visto real en el
#    CSV: "CHOPPER's Vol.1 carta/comic promocional EB02-003") -- eso NO es
#    el producto Extra Booster EB-02 en sí, es una carta suelta de dentro.
_DOUBLE_PACK_CODE_RE = re.compile(r"\bDP-?\d{1,2}\b(?!-\d)", re.IGNORECASE)
_EXTRA_BOOSTER_CODE_RE = re.compile(r"\bEB-?\d{1,2}\b(?!-\d)", re.IGNORECASE)

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

# Convención alternativa de código para Illustration Box, distinta de
# "Vol.N" (2026-08-27, revisión sobre datos reales de 53 tiendas): Distrito
# Zero/Gameria etiquetan "ILLUSTRATION BOX IB-06"/"Illustration Box IB-09"
# sin la palabra "Vol" en ningún sitio -- 56 store_product reales se
# quedaban con set_code=None por esto, agrupados todos juntos como si
# fueran "el mismo" producto sin identificar. Solo para ILLUSTRATION_BOX
# (no PLAYMAT, que no usa esta convención) -- mismo VOL{NN} de salida que
# _VOLUME_RE para que siga casando contra el set_code del canónico.
_ILLUSTRATION_BOX_CODE_RE = re.compile(r"\bIB[\s-]?0*(\d{1,3})\b", re.IGNORECASE)

# DP siempre gana sobre cualquier otro prefijo dentro de DOUBLE_PACK
# (2026-08-27, revisión sobre datos reales): varias tiendas mencionan el
# set OP-NN que acompaña al Double Pack como contexto ANTES del código real
# ("OP15 Double Pack Set DP-10 Adventure...") -- el _SET_CODE_PREFIXES
# genérico de más abajo hace re.search de IZQUIERDA a derecha y se quedaba
# con "OP15" (el primero en aparecer), no con el "DP-10" real, así que el
# gate de set_code en matcher._evaluate() comparaba códigos que nunca iban
# a coincidir con ningún canónico Double Pack. 13 store_product reales
# afectados. Si no hay código DP en el texto, se deja en None a propósito
# -- un OP-code decorativo NO es el identificador real de un Double Pack,
# usarlo como tal generaría un match falso, no uno aproximado.
_DOUBLE_PACK_SET_CODE_RE = re.compile(r"\bDP[\s-]?0*(\d{1,3})\b", re.IGNORECASE)


def _detect_language(text: Optional[str]) -> Optional[str]:
    """Detecta idioma en un fragmento de texto (nombre de producto o título de
    variante). El chequeo de "EN" es case-sensitive a propósito (sobre el texto
    ORIGINAL, no en minúsculas) para no confundir con la preposición española
    "en", que aparece constantemente en descripciones de productos."""
    if not text:
        return None
    lower = text.lower()
    # \bJP\b ademas de "japones"/"japonés" completo (2026-08-27,
    # investigación sobre multi_tienda_one_piece.csv real): 9 filas reales
    # usan el código suelto ("...One Piece Tcg [JP]", "...JP Edition") sin
    # la palabra completa -- mismo patrón que \bEN\b ya usaba para inglés,
    # solo que a este lado nunca se le había añadido el equivalente.
    if "japones" in lower or "japonés" in lower or re.search(r"\bJP\b", text):
        return "JP"
    if "coreano" in lower:
        return "KR"
    if "ingles" in lower or "inglés" in lower or re.search(r"\bEN\b", text) or "- en" in lower:
        return "EN"
    if "castellano" in lower or "español" in lower:
        return "ES"
    return None


def classify_product(
    name: Optional[str], variant_title: Optional[str] = None, extra_type_hint: Optional[str] = None,
) -> Classification:
    """Clasifica un producto por tipo/set/idioma a partir de su nombre y,
    opcionalmente, del título de su variante (usado como fallback de idioma
    cuando el nombre compartido del producto no lo delata -- p.ej. cuando
    "Inglés"/"Japonés" es una VARIANTE y no parte del título común, como pasa
    en Pokemillon: dos filas del mismo producto, una por variante, cada una
    con su propio stock).

    `extra_type_hint` (2026-08-27): señal estructurada adicional SOLO para
    el TIPO -- de momento, `store_product.raw_tags` (Shopify: campo `tags`
    nativo del comerciante, ej. "Caja One Piece, Cajas, Cajas de Sobres...").
    Verificado en vivo contra Pokemillon real: su `product_type` nativo de
    Shopify viene vacío siempre, pero `tags` sí trae señal fiable -- de ahí
    usar esto y no ese otro campo, pese a que en teoría es "el campo hecho
    para esto"."""
    if not name:
        return Classification("OTROS", None, None, None)

    name_lower = name.lower()
    # name+variant(+extra_type_hint) combinados para el TIPO (2026-08-27,
    # investigación sobre multi_tienda_one_piece.csv real: 540/1536 filas --
    # 35% del catálogo -- caían en OTROS porque la única palabra de tipo
    # vivía en `variant`, no en `name` -- ej. name="One Piece | OP-11 A
    # Fist of Divine Speed" (sin ninguna palabra de tipo) + variant="Sobre
    # Inglés"/"Caja 24 Sobres Inglés". set_code/main_set/idioma NO se tocan
    # aquí -- siguen derivándose de `name` (más abajo) e idioma de
    # name-o-variant como ya hacía antes, esto es solo para el product_type.
    type_search_text = (
        f"{name_lower} {variant_title.lower() if variant_title else ''} "
        f"{extra_type_hint.lower() if extra_type_hint else ''}"
    )

    product_type = "OTROS"
    for tipo, keywords in CLASSIFICATION_RULES:
        if any(kw in type_search_text for kw in keywords):
            product_type = tipo
            break

    if product_type == "OTROS" and _BOOSTER_PACK_PLURAL_RE.search(type_search_text):
        product_type = "BOOSTER_PACK"
    if product_type == "OTROS" and _DOUBLE_PACK_CODE_RE.search(type_search_text):
        product_type = "DOUBLE_PACK"
    if product_type == "OTROS" and _EXTRA_BOOSTER_CODE_RE.search(type_search_text):
        product_type = "BOOSTER_PACK"

    if product_type in _VOLUME_IDENTIFIED_PRODUCT_TYPES:
        # Ver _VOLUME_IDENTIFIED_PRODUCT_TYPES -- Vol.N, no un prefijo de
        # catálogo (que aquí sería el OP-set decorativo de acompañamiento).
        vol_match = _VOLUME_RE.search(name)
        if not vol_match and product_type == "ILLUSTRATION_BOX":
            # Ver _ILLUSTRATION_BOX_CODE_RE -- convención "IB-NN" alternativa
            # a "Vol.N", mismo VOL{NN} de salida.
            vol_match = _ILLUSTRATION_BOX_CODE_RE.search(name)
        set_code = f"VOL{int(vol_match.group(1)):02d}" if vol_match else None
    elif product_type == "DOUBLE_PACK":
        # Ver _DOUBLE_PACK_SET_CODE_RE -- DP siempre gana, nunca el genérico
        # de más abajo (que cogería el primer prefijo que aparezca en el
        # texto, incluyendo un OP-set decorativo de acompañamiento).
        dp_match = _DOUBLE_PACK_SET_CODE_RE.search(name)
        set_code = f"DP{int(dp_match.group(1)):02d}" if dp_match else None
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
    if language is None and "non-english" not in type_search_text and "non english" not in type_search_text:
        # Decisión (2026-08-27, revisión de métricas del matcher): si
        # ninguna tienda dice el idioma en absoluto, se asume Inglés -- es
        # la variante por defecto que vende cualquier tienda; cuando alguien
        # vende la variante JP, SIEMPRE lo marca explícitamente para
        # distinguirla de la que vende por defecto (esto es lo que ya
        # verificaba _detect_language: "japones"/"japonés"/\bJP\b). Antes de
        # este cambio, 190 de 836 store_product en needs_review tenían
        # set_code exacto y similarity>0.6 (score de confirmación) pero se
        # quedaban sin confirmar solo por no poder detectar idioma en el
        # texto -- un gate más conservador de lo necesario, no una
        # ambigüedad real.
        #
        # "non-english"/"non english" es la única excepción -- verificado en
        # catálogos de promos reales ("Rebecca (OP10-058) (V.1) Royal Blood
        # (Non-English)"): ahí el texto SÍ dice algo sobre el idioma, y es
        # justo lo contrario de "asumamos que es EN". Se deja en None (sigue
        # ambiguo) en vez de asumir mal.
        language = "EN"

    return Classification(product_type, set_code, language, main_set)


def classify_with_category(
    name: Optional[str], variant_title: Optional[str] = None, extra_type_hint: Optional[str] = None,
) -> tuple[Classification, Optional[str]]:
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
    classification = classify_product(name, variant_title, extra_type_hint)
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

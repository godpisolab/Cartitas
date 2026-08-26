"""Tests de classify_product() / _detect_language() -- sección 1.1 del plan
de pruebas. Lógica de reglas de texto pura, sin red ni BBDD: el mayor
retorno por hora invertida, según la propia prioridad del plan.

Nota sobre el dataclass Classification: sus campos van en el orden
(product_type, set_code, language, main_set) -- DISTINTO del orden en que
se suele hablar de ellos en prosa ("tipo, set, idioma"). Los tests de abajo
acceden siempre por nombre de atributo, nunca por posición, para no
introducir un desajuste silencioso entre el orden "natural" de lectura y
el orden real del dataclass.
"""

from __future__ import annotations

import pytest

from base_script import classify_product, _detect_language


class TestClassifyProductTable:
    """Tabla de casos parametrizada (sección 1.1 del plan) -- cada fila es
    (name, variant_title, product_type, set_code, language, main_set)."""

    CASES = [
        pytest.param(
            "Booster Box OP-16 The Time of Battle", None,
            "BOOSTER_BOX", "OP16", None, "OP16",
            id="booster-box-con-guion",
        ),
        pytest.param(
            "Starter Deck ST-21 Gear 5 Inglés", None,
            "STARTER_DECK", "ST21", "EN", None,
            id="starter-deck-idioma-explicito",
        ),
        pytest.param(
            "Sobre One Piece OP16 (Japonés)", None,
            "BOOSTER_PACK", "OP16", "JP", "OP16",
            id="booster-pack-sin-guion-japones",
        ),
        pytest.param(
            "Lote 50 cartas sueltas One Piece", None,
            "LOTE_CARTAS", None, None, None,
            id="lote-cartas",
        ),
        pytest.param(
            "Premium Card Collection The Best Vol.2", None,
            "PREMIUM_COLLECTION", None, None, None,
            id="premium-collection-vol-minuscula-no-matchea-set-code",
        ),
        pytest.param(
            "Funda protectora genérica", None,
            "OTROS", None, None, None,
            id="sin-keyword-cae-a-otros",
        ),
        pytest.param(
            None, None,
            "OTROS", None, None, None,
            id="name-none-no-revienta",
        ),
        pytest.param(
            "", None,
            "OTROS", None, None, None,
            id="name-cadena-vacia",
        ),
        pytest.param(
            "Mazo de inicio Buggy", None,
            "STARTER_DECK", None, None, None,
            id="keyword-en-espanol",
        ),
        pytest.param(
            "One Piece TCG", "Inglés",
            "OTROS", None, "EN", None,
            id="idioma-por-fallback-de-variant-title",
        ),
        pytest.param(
            "EN Booster Box OP-16", None,
            "BOOSTER_BOX", "OP16", "EN", "OP16",
            id="EN-mayuscula-como-codigo-de-idioma-real",
        ),
    ]

    @pytest.mark.parametrize("name,variant_title,product_type,set_code,language,main_set", CASES)
    def test_classification_table(self, name, variant_title, product_type, set_code, language, main_set):
        result = classify_product(name, variant_title)
        assert result.product_type == product_type
        assert result.set_code == set_code
        assert result.language == language
        assert result.main_set == main_set


class TestSpanishPrepositionRegression:
    """Regresión explícita del caso documentado en el propio código: "en"
    como preposición española NO debe detectarse como idioma inglés. La
    protección es \\bEN\\b case-sensitive sobre el texto ORIGINAL (no en
    minúsculas) -- un test que fija esto evita que alguien "simplifique" el
    detector de idioma sin darse cuenta de que rompe el español."""

    def test_preposicion_en_minuscula_no_es_idioma(self):
        assert _detect_language("Guía en 3 pasos para jugar") is None

    def test_en_mayuscula_como_palabra_suelta_si_es_idioma(self):
        assert _detect_language("Booster Box EN") == "EN"

    def test_en_minuscula_dentro_de_otra_palabra_no_matchea(self):
        # "en" como substring de otra palabra (no un token propio) tampoco
        # debe disparar el regex \bEN\b (los límites de palabra lo evitan).
        assert _detect_language("Entrada genérica sin idioma") is None


class TestDetectLanguageOtrosIdiomas:
    """Ramas de _detect_language sin cubrir hasta ahora (coreano/castellano).

    HALLAZGO al escribir esto: `_detect_language` puede devolver "KR", pero
    el ENUM `product_language` del esquema solo admite ('EN', 'JP', 'ES')
    -- no hay 'KR'. No revienta nada hoy (store_product no guarda `language`
    directamente, solo se deriva en caliente al matchear), pero si algún día
    se compara una Classification.language=='KR' contra product.language,
    nunca podrá haber un producto canónico coreano con el que igualarlo.
    Documentado, no corregido sin que se pida."""

    def test_coreano(self):
        assert _detect_language("One Piece Booster Box Coreano") == "KR"

    def test_castellano(self):
        assert _detect_language("One Piece Booster Box Castellano") == "ES"

    def test_espanol_con_enie(self):
        assert _detect_language("One Piece Booster Box Español") == "ES"


class TestMainSetVsSetCode:
    """OP16 y OP17 en el mismo título -- re.search se queda con la PRIMERA
    coincidencia (no hay re.findall). Se documenta el comportamiento
    explícitamente, no se deja implícito."""

    def test_primera_coincidencia_gana_para_main_set_y_set_code(self):
        result = classify_product("OP16 y OP17 en el mismo título")
        assert result.main_set == "OP16"
        assert result.set_code == "OP16"

    def test_preposicion_en_no_se_confunde_con_idioma_en_este_texto(self):
        # Este texto contiene " en " en minúscula (preposición) en medio de
        # la frase -- no debe colarse como EN pese a estar rodeado de
        # códigos de set en mayúscula.
        result = classify_product("OP16 y OP17 en el mismo título")
        assert result.language is None


class TestMainSetDigitBoundary:
    """Límite real de la regex de main_set: \\bOP[\\s-]?0*(\\d{1,2})\\b.
    El plan original proponía "ST-100 (dos dígitos) vs ST-999 (tres
    dígitos)" para esto, pero eso no ejercita main_set en absoluto -- esa
    regex exige literalmente el prefijo "OP", nunca "ST". El límite real
    que puede fallar es OP-99 (2 dígitos, dentro de \\d{1,2}) frente a
    OP-100 (3 dígitos): el \\b final después de \\d{1,2} impide un match
    parcial de solo 2 de los 3 dígitos, así que OP-100 no matchea nada."""

    def test_op_de_dos_digitos_en_el_limite_matchea(self):
        result = classify_product("Booster Box OP-99")
        assert result.main_set == "OP99"

    def test_op_de_tres_digitos_no_matchea_por_el_limite_de_dos(self):
        result = classify_product("Booster Box OP-100")
        assert result.main_set is None

    def test_set_code_st_con_tres_digitos_si_matchea_su_propio_limite(self):
        # set_code (\b([A-Z]{2,3}-?\d{1,3})\b) sí admite hasta 3 dígitos --
        # a diferencia de main_set, que solo admite prefijo OP y 1-2 dígitos.
        result = classify_product("Starter Deck ST-100")
        assert result.set_code == "ST100"
        assert result.main_set is None

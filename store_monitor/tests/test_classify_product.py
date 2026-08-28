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

from shared.classify import classify_product, classify_with_category, _detect_language


class TestClassifyProductTable:
    """Tabla de casos parametrizada (sección 1.1 del plan) -- cada fila es
    (name, variant_title, product_type, set_code, language, main_set)."""

    CASES = [
        pytest.param(
            "Booster Box OP-16 The Time of Battle", None,
            "BOOSTER_BOX", "OP16", "EN", "OP16",
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
            "LOTE_CARTAS", None, "EN", None,
            id="lote-cartas",
        ),
        pytest.param(
            "Premium Card Collection The Best Vol.2", None,
            "PREMIUM_COLLECTION", None, "EN", None,
            id="premium-collection-vol-minuscula-no-matchea-set-code",
        ),
        pytest.param(
            "Funda protectora genérica", None,
            "OTROS", None, "EN", None,
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
            "STARTER_DECK", None, "EN", None,
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
        pytest.param(
            "KID – STARTER DECK ONE PIECE – ST 36", None,
            "STARTER_DECK", "ST36", "EN", None,
            id="set-code-con-espacio-en-vez-de-guion-caso-real-arte9",
        ),
        pytest.param(
            "LEARN TOGETHER DECK SET – STARTER DECKS ONE PIECE", None,
            "LEARN_DECK", None, "EN", None,
            id="learn-deck-gana-a-starter-deck-pese-a-contener-starter-decks",
        ),
        pytest.param(
            "ONE PIECE CARD GAME - THE BEST VOL2 - PRB02 - EN", None,
            "PREMIUM_COLLECTION", "PRB02", "EN", None,
            id="prefijo-generico-VOL-no-es-set-code-solo-prefijos-de-la-lista-blanca",
        ),
        pytest.param(
            # 2026-08-27: IB-NN SÍ se reconoce ahora (ver
            # TestIllustrationBoxCodigoIB) -- convención real de
            # Distrito Zero/Gameria, distinta de "Vol.N".
            "ONE PIECE CARD GAME - ILLUSTRATION BOX IB-06 - EN", None,
            "ILLUSTRATION_BOX", "VOL06", "EN", None,
            id="prefijo-IB-se-reconoce-como-volumen-de-illustration-box",
        ),
        pytest.param(
            "One Piece Illustration Box Vol.6 Law & Rosinante OP13", None,
            "ILLUSTRATION_BOX", "VOL06", "EN", "OP13",
            id="illustration-box-usa-vol-no-el-op-decorativo-de-acompanamiento",
        ),
        pytest.param(
            "One Piece Playmat Limited Edition Vol.1 + 3 Sobres OP-15", None,
            "PLAYMAT", "VOL01", "EN", "OP15",
            id="playmat-usa-vol-no-el-op-decorativo-de-acompanamiento",
        ),
        pytest.param(
            "Official Playmat Limited Edition Vol.3 EN", None,
            "PLAYMAT", "VOL03", "EN", None,
            id="playmat-vol-de-un-solo-digito-se-normaliza-con-cero",
        ),
        # 2026-08-27: investigación sobre multi_tienda_one_piece.csv real
        # (1536 filas, 30 tiendas) -- 35% del catálogo caía en OTROS porque
        # la única palabra de tipo vivía en `variant`, nunca en `name`.
        pytest.param(
            "One Piece | OP-11 A Fist of Divine Speed", "Sobre Inglés",
            "BOOSTER_PACK", "OP11", "EN", "OP11",
            id="tipo-solo-en-variant-nunca-en-name-caso-real-pokemillon-sobre",
        ),
        pytest.param(
            "One Piece | OP-11 A Fist of Divine Speed", "Caja 24 Sobres Inglés",
            "BOOSTER_BOX", "OP11", "EN", "OP11",
            id="tipo-solo-en-variant-nunca-en-name-caso-real-pokemillon-caja",
        ),
        pytest.param(
            "Mazo ST-22 Ace & Shirohige", None,
            "STARTER_DECK", "ST22", "EN", None,
            id="mazo-a-secas-sin-de-inicio-no-colisiona-con-amazon",
        ),
        pytest.param(
            "ONE PIECE TCG - DOBLE PACK SET VOL. 14 (DP-14)", "",
            "DOUBLE_PACK", "DP14", "EN", None,
            id="doble-pack-variante-en-espanol",
        ),
        pytest.param(
            "Pack 5 Sobres One Piece Adventure on KAMI’s Island OP15 - Japones", "Default Title",
            "BOOSTER_PACK", "OP15", "JP", "OP15",
            id="sobres-plural-no-matchea-sobre-con-espacio-necesita-fallback",
        ),
        pytest.param(
            "Rebecca (OP10-058) (V.1) Royal Blood (Non-English)", "CGC 10",
            "LOTE_CARTAS", "OP10", None, "OP10",
            id="cgc-carta-individual-gradeada-gana-a-cualquier-otra-keyword",
        ),
        pytest.param(
            "Monkey.D.Luffy (P-001) 7 - Eleven Promos", "PSA 10",
            "LOTE_CARTAS", None, "EN", None,
            id="psa-carta-individual-gradeada",
        ),
        pytest.param(
            "ONE PIECE TCG - EB-05", "",
            "BOOSTER_PACK", "EB05", "EN", None,
            id="codigo-eb-sin-ninguna-palabra-de-tipo-alrededor-fallback",
        ),
        pytest.param(
            "ONE PIECE CHOPPER’s Vol. 1 carta/comic promocional EB02-003", "",
            "OTROS", "EB02", "EN", None,
            id="codigo-eb-de-CARTA-INDIVIDUAL-eb02-003-no-dispara-el-fallback-de-booster",
        ),
        pytest.param(
            "(CASE) THE BEST 2 – PRB-02 – x10 Booster Box- One Piece Card Game", None,
            "BOOSTER_CASE", "PRB02", "EN", None,
            id="booster-case-parentesis-case",
        ),
        pytest.param(
            "Case - Booster Box OP-16 The Time of Battle", None,
            "BOOSTER_CASE", "OP16", "EN", "OP16",
            id="booster-case-gana-a-booster-box-por-orden-de-lista",
        ),
        pytest.param(
            "One Piece Card Game Booster Box Case ST-05", None,
            "BOOSTER_CASE", "ST05", "EN", None,
            id="booster-box-case-keyword-completa",
        ),
        pytest.param(
            # docs/pendientes-motor-matching.md punto 2 -- caso real que
            # SÍ confirmó mal en producción de prueba (commit 161bae5): un
            # accesorio ("Card Case", una funda de cartas) coló como
            # BOOSTER_CASE por el patrón genérico "case -" que existía
            # entonces. No tiene ni palabra de contexto (caja/booster/
            # sellado) ni código de set -- debe quedar excluido.
            "Limited Card Case -Monkey.D.Luffy-", None,
            "OTROS", None, "EN", None,
            id="card-case-accesorio-no-es-booster-case-regresion-161bae5",
        ),
        pytest.param(
            # Caso real (multi_tienda_one_piece.csv, tienda Master of
            # Games): "Case" sin ninguna palabra de caja/booster pegada --
            # solo el código de set. Debe seguir confirmando BOOSTER_CASE
            # vía el código, no solo vía la palabra de contexto.
            "[PREORDER] [INGLÉS] One Piece Card Game OP-19 Case", None,
            "BOOSTER_CASE", "OP19", "EN", "OP19",
            id="case-sin-booster-ni-box-pegado-confirma-por-codigo-de-set",
        ),
        pytest.param(
            # Caso real (FreakCorp): "Case" + "cajas" (contexto en
            # español) + código de set, sin ninguna keyword de tipo en
            # inglés.
            "Case OP02 Paramount War (12 cajas)", None,
            "BOOSTER_CASE", "OP02", "EN", "OP02",
            id="case-con-contexto-cajas-en-espanol",
        ),
        pytest.param(
            # "Dice Case" (un estuche de dados) nunca debe colar como
            # BOOSTER_CASE -- ninguna palabra de contexto de caja/booster/
            # sellado, ningún código de set.
            "One Piece TCG Official Dice and Dice Case Set", None,
            "DICE_ACCESSORY", None, "EN", None,
            id="dice-case-no-es-booster-case",
        ),
        pytest.param(
            # "Playmat and Card Case Set" -- PLAYMAT gana por orden de
            # lista (se comprueba antes), y BOOSTER_CASE tampoco tiene
            # señal de contexto/código aquí de todas formas.
            "One Piece Card Game - Playmat and Card Case Set -25th Edition-", None,
            "PLAYMAT", None, "EN", None,
            id="playmat-y-card-case-no-es-booster-case",
        ),
        pytest.param(
            # "Case" mencionado sin ningún contexto de producto sellado ni
            # código de set reconocible -- un producto Funko Pop real del
            # CSV, no relacionado con Booster Case en absoluto.
            "Funko Pop! One Piece - Case 5+1 Jewelry Bonney", None,
            "OTROS", None, "EN", None,
            id="case-sin-contexto-ni-codigo-no-es-booster-case",
        ),
        pytest.param(
            # docs/pendientes-motor-matching.md punto 7 -- caso real sin
            # ningún código DF explícito, solo el volumen. "caja" por sí
            # sola habría capturado esto como BOOSTER_BOX antes de llegar a
            # LEARN_DECK/DEVIL_FRUITS_COLLECTION si el orden de la lista no
            # protegiera -- aquí protege por el orden ya existente
            # (DEVIL_FRUITS_COLLECTION va antes que BOOSTER_BOX).
            "Caja One Piece Devil Fruits Collection Vol.2 - Ingles", None,
            "DEVIL_FRUITS_COLLECTION", "DF02", "EN", None,
            id="devil-fruits-collection-sin-codigo-df-explicito-usa-vol-como-fallback",
        ),
        pytest.param(
            # keyword en español, mismo patrón que "mazo"/"doble pack" --
            # sin esto, "caja" capturaba como BOOSTER_BOX antes de llegar a
            # LEARN_DECK.
            "One Piece | Caja Aprende a Jugar", None,
            "LEARN_DECK", None, "EN", None,
            id="learn-deck-aprende-a-jugar-en-espanol",
        ),
        pytest.param(
            # docs/pendientes-motor-matching.md punto 4 -- caso real
            # confirmado: la carta suelta de regalo DON!! (viene DENTRO de
            # Double Pack Set Vol.10, vendida por separado) coincidía con
            # el fallback de código DP-NN y confirmaba contra el Double
            # Pack Set sellado completo -- precio y unidad de venta
            # completamente distintos. Sin "set"/"pack"/"caja"/"box"/
            # "sobre" que confirme que es el sellado -> PROMO_CARD.
            "Don!! (DP10 Map) - One Piece Products (DON!!)", "English / Near Mint / Normal",
            "PROMO_CARD", "DP10", "EN", None,
            id="don-card-suelta-sin-contexto-de-sellado-es-promo-card",
        ),
        pytest.param(
            # Control negativo -- "Pack" en el mismo texto SÍ confirma que
            # es el sellado completo, se queda como DOUBLE_PACK (caso real,
            # misma tienda, mismo patrón DON!!+código).
            "One Piece Special DON!! Card Pack DP-06 - Emperors in the New World (OP09)", "Inglés",
            "DOUBLE_PACK", "DP06", "EN", "OP09",
            id="don-card-pack-con-pack-en-el-texto-sigue-siendo-double-pack",
        ),
        pytest.param(
            # Control negativo -- el propio Double Pack Set sellado que
            # contiene la carta DON!! de regalo, no debe verse afectado
            # (ya clasifica por la keyword "double pack" en el bucle
            # principal, nunca llega al fallback con el guard nuevo).
            "One Piece Card Game Double Pack Set Vol.10 [DP-10] – 2 Booster Packs + Exclusive DON!! Card", None,
            "DOUBLE_PACK", "DP10", "EN", None,
            id="double-pack-set-completo-con-don-card-de-regalo-mencionada-no-se-ve-afectado",
        ),
    ]

    @pytest.mark.parametrize("name,variant_title,product_type,set_code,language,main_set", CASES)
    def test_classification_table(self, name, variant_title, product_type, set_code, language, main_set):
        result = classify_product(name, variant_title)
        assert result.product_type == product_type
        assert result.set_code == set_code
        assert result.language == language
        assert result.main_set == main_set


class TestExtraTypeHintDeTags:
    """extra_type_hint (2026-08-27): señal estructurada adicional -- de
    momento, `store_product.raw_tags` de Shopify. Verificado en vivo contra
    Pokemillon real: su `product_type` nativo de Shopify viene siempre
    vacío, pero `tags` sí trae señal fiable, de ahí usar esto."""

    def test_tags_resuelve_tipo_que_ni_name_ni_variant_traen(self):
        # Caso real de Pokemillon (verificado en vivo, 2026-08-27): ni
        # `name` ni `variant` mencionan "caja"/"booster"/etc -- solo `tags`
        # lo dice ("Cajas de Sobres").
        result = classify_product(
            "One Piece OP13 Carrying On His Will", "Inglés",
            "Caja One Piece, Cajas, Cajas de Sobres, One Piece, OP-13 Carrying On His Will",
        )
        assert result.product_type == "BOOSTER_BOX"
        assert result.set_code == "OP13"
        assert result.language == "EN"

    def test_sin_tags_el_mismo_producto_se_queda_en_otros(self):
        # Mismo caso de arriba, sin el tercer argumento -- confirma que la
        # mejora viene de tags, no de otra cosa (name/variant solos no
        # bastan para este ejemplo real).
        result = classify_product("One Piece OP13 Carrying On His Will", "Inglés")
        assert result.product_type == "OTROS"

    def test_tags_none_no_revienta(self):
        result = classify_product("One Piece OP13 Carrying On His Will", "Inglés", None)
        assert result.product_type == "OTROS"

    def test_tags_no_pisa_una_palabra_de_tipo_ya_clara_en_name(self):
        # Si name/variant ya lo dejan claro, tags es irrelevante -- no debe
        # poder CAMBIAR una clasificación ya correcta a otra cosa.
        result = classify_product(
            "Starter Deck ST-21 Gear 5 Inglés", None, "Cajas, Cajas de Sobres",
        )
        assert result.product_type == "STARTER_DECK"

    def test_codigo_dp_gana_a_tag_generico_de_caja(self):
        # Caso real (2026-08-28, docs/propuesta-mejoras-matching-sesion.md
        # punto 0/1): "One Piece | DP-07 A Fist of Divine Speed OP-11" con
        # la tag genérica "Cajas" caía en BOOSTER_BOX en vez de
        # DOUBLE_PACK -- name+variant a secas no dan ningún tipo (name_only
        # queda OTROS), así que el tipo actual venía SOLO de la tag, y el
        # código DP-07 en el propio texto es señal más fiable.
        result = classify_product(
            "One Piece | DP-07 A Fist of Divine Speed OP-11", "Inglés",
            "Caja One Piece, Cajas, One Piece, OP-11 A Fist of Divine Speed",
        )
        assert result.product_type == "DOUBLE_PACK"
        assert result.set_code == "DP07"

    def test_tags_con_case_solo_no_dispara_booster_case(self):
        # Caso real (Golden Pulls, encontrado en una siembra completa
        # contra Postgres real, 2026-08-28): "Booster Box Display OP11" no
        # menciona "case" en ningún sitio del nombre -- solo en sus
        # `raw_tags` de catálogo ("ace, box, card game, case, luffy, one
        # piece, op11, selladoingles"), un tag reutilizado y ruidoso, no
        # una descripción real del producto. Antes de este fix, confirmaba
        # incorrectamente contra un Case de 12 cajas siendo, con toda
        # probabilidad, 1 caja suelta ("Display").
        result = classify_product(
            "One Piece Card Game: Booster Box Display OP11 - A Fist of Divine Speed", "Default Title",
            "ace, box, card game, case, luffy, one piece, op11, selladoingles",
        )
        assert result.product_type == "BOOSTER_BOX"

    def test_classify_with_category_tambien_acepta_el_tercer_argumento(self):
        classification, category_slug = classify_with_category(
            "One Piece OP13 Carrying On His Will", "Inglés",
            "Caja One Piece, Cajas, Cajas de Sobres, One Piece, OP-13 Carrying On His Will",
        )
        assert classification.product_type == "BOOSTER_BOX"
        assert category_slug == "booster-box"


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


class TestJPSueltoAdemasDePalabraCompleta:
    """2026-08-27, investigación sobre multi_tienda_one_piece.csv real: 9
    filas reales usan el código suelto "JP" (ej. "...One Piece Tcg [JP]",
    "...JP Edition") sin la palabra completa "japonés"/"japones" -- \\bJP\\b
    sobre el texto original, mismo patrón que ya tenía \\bEN\\b para inglés
    (que a este lado nunca se le había añadido el equivalente)."""

    def test_jp_suelto_entre_corchetes_caso_real(self):
        assert _detect_language("Sobre Booster OP-14: The Azure Sea's Seven - One Piece Tcg [JP]") == "JP"

    def test_palabra_completa_japones_sigue_funcionando(self):
        assert _detect_language("One Piece TCG OP16 Booster Box (Japones)") == "JP"

    def test_jp_minuscula_dentro_de_otra_palabra_no_matchea(self):
        # Con \bJP\b sobre el texto ORIGINAL (case-sensitive, como \bEN\b),
        # "jp" en minúscula dentro de otra palabra no debe disparar nada.
        assert _detect_language("Grupo Jpepe importaciones") is None


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
        # códigos de set en mayúscula. Se comprueba _detect_language()
        # directamente, no classify_product() -- desde el default a EN
        # (2026-08-27) cuando nadie dice el idioma, el resultado final de
        # classify_product() para este texto SÍ es "EN" (por el default, no
        # por la preposición), así que la aserción real de este test vive
        # en la función de detección, no en el resultado combinado.
        assert _detect_language("OP16 y OP17 en el mismo título") is None


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


class TestDoublePackPrefiereCodigoDP:
    """Regresión real (2026-08-27, revisión sobre 53 tiendas): un Double
    Pack a menudo menciona el set OP-NN que acompaña como contexto ANTES
    del código DP-NN real -- el prefijo genérico se quedaba con el primero
    que aparece en el texto (izquierda a derecha), no con el DP real."""

    def test_op_antes_que_dp_en_el_texto_se_queda_con_dp(self):
        result = classify_product("One Piece | OP15 Double Pack Set DP-10 Adventure on KAMI's Island")
        assert result.product_type == "DOUBLE_PACK"
        assert result.set_code == "DP10"
        assert result.main_set == "OP15"  # main_set sigue derivándose por separado, sin cambios

    def test_vol_y_op_y_dp_juntos_se_queda_con_dp(self):
        result = classify_product("DOUBLE PACK SET VOL.08 OP-12 DP-08 LEGACY OF THE MASTER ONE PIECE TCG")
        assert result.set_code == "DP08"

    def test_sin_codigo_dp_en_el_texto_set_code_es_none(self):
        # Un OP-code decorativo sin DP-code real no debe usarse como si lo
        # fuera -- mejor None que un match falso.
        result = classify_product("One Piece Card Game Double Pack Set OP-14")
        assert result.set_code is None


class TestIllustrationBoxCodigoIB:
    """Regresión real (2026-08-27, revisión sobre 53 tiendas): Distrito
    Zero/Gameria etiquetan Illustration Box como "IB-NN", sin la palabra
    "Vol" en ningún sitio -- _VOLUME_RE (solo "Vol.N") dejaba set_code=None."""

    def test_codigo_ib_con_guion(self):
        result = classify_product("ONE PIECE CARD GAME - ILLUSTRATION BOX IB-06 - EN")
        assert result.product_type == "ILLUSTRATION_BOX"
        assert result.set_code == "VOL06"

    def test_codigo_ib_sin_guion_entre_letra_y_numero(self):
        result = classify_product("One Piece Card Game Illustration Box IB09 (Inglés)")
        assert result.set_code == "VOL09"

    def test_vol_sigue_teniendo_prioridad_si_esta_presente(self):
        result = classify_product("Illustration Box Vol.6 Law & Rosinante IB-06 OP13")
        assert result.set_code == "VOL06"


class TestClassifyWithCategory:
    """classify_with_category() combina classify_product() con el mapeo a
    category.slug -- existe para que api/services/matches.py y matcher.py
    no repitan `PRODUCT_TYPE_TO_CATEGORY_SLUG.get(classification.product_type)`
    cada uno por su lado (ver decisión de arquitectura sobre shared/)."""

    def test_product_type_con_categoria_sembrada_devuelve_su_slug(self):
        classification, category_slug = classify_with_category("Booster Box OP-16")
        assert classification.product_type == "BOOSTER_BOX"
        assert category_slug == "booster-box"

    def test_not_applicable_product_type_devuelve_slug_none(self):
        classification, category_slug = classify_with_category("Lote 50 cartas sueltas")
        assert classification.product_type == "LOTE_CARTAS"
        assert category_slug is None

    def test_variant_title_se_propaga_igual_que_en_classify_product(self):
        classification, _ = classify_with_category("One Piece TCG", "Inglés")
        assert classification.language == "EN"


class TestDFCodePorPatronDeCodigo:
    """docs/propuesta-mejoras-matching-sesion.md punto 1 -- DF-NN identifica
    Devil Fruits Collection sin depender de la keyword en inglés ("devil
    fruits collection"), verificado como prefijo exclusivo en las 194
    líneas del catálogo oficial completo."""

    @pytest.mark.parametrize("raw_name,esperado_set_code", [
        ("One Piece | Fruta del Diablo vol.1 [DF-01] Inglés 2023", "DF01"),
        ("One Piece | Fruta del Diablo Mera Mera vol. 2 [DF-02]", "DF02"),
        ("One Piece | DF03 Fruta del Diablo Ope Ope No Mi vol. 3 OP12", "DF03"),
    ])
    def test_df_code_clasifica_devil_fruits_sin_depender_del_idioma(self, raw_name, esperado_set_code):
        c = classify_product(raw_name)
        assert c.product_type == "DEVIL_FRUITS_COLLECTION"
        assert c.set_code == esperado_set_code

    def test_df_code_gana_pese_a_tag_generico_de_caja(self):
        # Caso real (2026-08-28): la tienda tiene "Cajas" como tag de
        # catálogo genérico -- sin el código como señal, esto colaba como
        # BOOSTER_BOX en vez de DEVIL_FRUITS_COLLECTION.
        c = classify_product(
            "One Piece | DF03 Fruta del Diablo Ope Ope No Mi vol. 3  OP12", "Inglés",
            "Black Friday, Caja One Piece, Cajas, Devil Fruit, One Piece",
        )
        assert c.product_type == "DEVIL_FRUITS_COLLECTION"
        assert c.set_code == "DF03"

    def test_keyword_real_en_el_nombre_sigue_ganando_al_codigo(self):
        # Si el propio nombre YA trae una keyword real de otro tipo, esa
        # señal manda -- el código DF-NN no debe poder pisarla (el override
        # solo se dispara cuando name+variant a secas no dicen nada).
        c = classify_product("Booster Box Something DF-01", None)
        assert c.product_type == "BOOSTER_BOX"


class TestRangoDeCodigos:
    """docs/propuesta-mejoras-matching-sesion.md punto 2 -- un rango de
    códigos ("ST-15 - ST-20", "[ST-31]~[ST-36]") describe un pack ligado a
    TODO un lote de mazos, no a uno solo -- extraer el primer extremo como
    si fuera el código propio dispara el fallback cross-categoría contra
    un Starter Deck completamente ajeno."""

    @pytest.mark.parametrize("raw_name", [
        "One Piece Sobre ST-15 - ST-20 Release Event Pack",
        "One Piece Sobre Beginners Deck Party ST-31 - ST-36 Participation Pack",
        "One Piece Sobre Beginners Deck Party [ST-31]~[ST-36] Winner pack",
    ])
    def test_rango_de_codigos_no_extrae_el_primer_extremo(self, raw_name):
        c = classify_product(raw_name)
        assert c.set_code is None

    def test_codigo_de_carta_individual_no_se_confunde_con_rango(self):
        # Regresión encontrada al validar el punto 2: "OP10-058" (numeración
        # de carta individual dentro de un set, no un rango entre dos sets)
        # NO debe perder su set_code -- ya cubierto por LOTE_CARTAS/CGC,
        # pero el propio código debe seguir extrayéndose bien.
        c = classify_product("Rebecca (OP10-058) (V.1) Royal Blood (Non-English)", "CGC 10")
        assert c.set_code == "OP10"

    def test_codigo_eb_de_carta_individual_no_se_confunde_con_rango(self):
        c = classify_product("ONE PIECE CHOPPER’s Vol. 1 carta/comic promocional EB02-003", "")
        assert c.set_code == "EB02"

"""Tests de classify_product() / _detect_language() -- Recognition Pipeline
(docs/propuestas/guia_nuevo_matcher.md). Lógica de reglas de texto pura, sin
red ni BBDD.

Nota sobre el dataclass Classification: sus campos van en el orden
(product_type, set_code, language, main_set, packaging) -- DISTINTO del
orden en que se suele hablar de ellos en prosa. Los tests de abajo acceden
siempre por nombre de atributo, nunca por posición, para no introducir un
desajuste silencioso entre el orden "natural" de lectura y el orden real
del dataclass.

Taxonomía nueva vs. la anterior: BOOSTER_BOX/BOOSTER_PACK/BOOSTER_CASE se
funden en ONE_PIECE (packaging='display'/'sobre'/'case' sustituye a la
categoría); PREMIUM_COLLECTION se separa en PREMIUM_CARD_COLLECTION y
PREMIUM_BOOSTER_BOX (dos productos distintos, no una variante de
empaquetado); LEARN_DECK se funde en STARTER_DECK; EXTRA_BOOSTER y SLEEVES
son família nuevas (antes vivían sin categoría propia, dentro de
BOOSTER_PACK/BOX vía fallback de código, o directamente en OTROS).
PROMO_CARD/MYSTERY_PACK/DICE_ACCESSORY suben a Fase 0 (not_applicable
siempre, igual que LOTE_CARTAS) -- ya no son "tipos de producto" con
categoría propia, aunque `classify_product()` los sigue devolviendo como
`product_type` para que el matcher los excluya explícitamente.
"""

from __future__ import annotations

import pytest

from shared.classify import classify_product, classify_with_category, _detect_language


class TestClassifyProductTable:
    """Tabla de casos parametrizada -- cada fila es (name, variant_title,
    product_type, set_code, language, main_set, packaging)."""

    CASES = [
        pytest.param(
            "Booster Box OP-16 The Time of Battle", None,
            "ONE_PIECE", "OP16", "EN", "OP16", "display",
            id="one-piece-caja-con-guion",
        ),
        pytest.param(
            "Starter Deck ST-21 Gear 5 Inglés", None,
            "STARTER_DECK", "ST21", "EN", None, "sobre",
            id="starter-deck-idioma-explicito",
        ),
        pytest.param(
            "Sobre One Piece OP16 (Japonés)", None,
            "ONE_PIECE", "OP16", "JP", "OP16", "sobre",
            id="one-piece-sobre-sin-guion-japones",
        ),
        pytest.param(
            "Lote 50 cartas sueltas One Piece", None,
            "LOTE_CARTAS", None, "EN", None, None,
            id="lote-cartas",
        ),
        pytest.param(
            # PREMIUM_CARD_COLLECTION comprueba Vol.N ANTES que su tabla de
            # ediciones propia (§4.3) -- família aparte de PREMIUM_BOOSTER_BOX,
            # nunca comparte tabla con OP/PRB/EB.
            "Premium Card Collection Vol.2 Baroque Works", None,
            "PREMIUM_CARD_COLLECTION", "VOL02", "EN", None, None,
            id="premium-card-collection-vol-tiene-prioridad-sobre-tabla-de-edicion",
        ),
        pytest.param(
            # Mismo título "The Best" que arriba usaba Vol.N, pero esta vez
            # es un Premium BOOSTER (família distinta, código PRB propio) --
            # nunca colisiona con Premium Card Collection porque cada
            # família solo reconoce SU PROPIA keyword ("premium card
            # collection" vs código/tabla PRB).
            "Premium Booster: The Best Vol.2", None,
            "PREMIUM_BOOSTER_BOX", "PRB02", "EN", None, "sobre",
            id="premium-booster-box-title-vol2-no-es-premium-card-collection",
        ),
        pytest.param(
            # Nueva família SLEEVES (antes caía en OTROS) -- singular, no
            # solo plural (21 de 194 productos reales del catálogo oficial
            # se nombran en singular).
            "Limited Card Sleeve", None,
            "SLEEVES", None, "EN", None, None,
            id="sleeves-singular-antes-caia-en-otros",
        ),
        pytest.param(
            # La línea numerada oficial "Official Sleeves 1-16" no lleva la
            # palabra "Vol." -- fallback de número suelto tras "sleeve(s)".
            "Official Sleeves 11", None,
            "SLEEVES", "VOL11", "EN", None, None,
            id="sleeves-numero-suelto-sin-vol-linea-oficial-1-16",
        ),
        pytest.param(
            "Official Card Sleeves 14", None,
            "SLEEVES", "VOL14", "EN", None, None,
            id="sleeves-numero-suelto-card-sleeves",
        ),
        pytest.param(
            # "Vol." explícito sigue ganando sobre el fallback de número
            # suelto cuando ambos podrían aplicar (otra línea de fundas).
            "Limited Card Sleeve Premium Matte vol.3", None,
            "SLEEVES", "VOL03", "EN", None, None,
            id="sleeves-vol-explicito-tiene-prioridad-sobre-numero-suelto",
        ),
        pytest.param(
            # Abreviatura "v.NN" -- fallback SOLO de sleeves (no toca
            # _VOLUME_RE compartido con DF/Illustration Box/Playmat/Premium
            # Card Collection).
            "Fundas v.16", None,
            "SLEEVES", "VOL16", "EN", None, None,
            id="sleeves-abreviatura-v-punto-numero",
        ),
        pytest.param(
            "Fundas v 9", None,
            "SLEEVES", "VOL09", "EN", None, None,
            id="sleeves-abreviatura-v-espacio-numero-sin-punto",
        ),
        pytest.param(
            # El número suelto tras "sleeve(s)" (línea oficial 1-16) sigue
            # ganando sobre "v.NN" cuando ambos podrían aplicar.
            "Official Sleeves 11 v.99", None,
            "SLEEVES", "VOL11", "EN", None, None,
            id="sleeves-numero-tras-sleeves-tiene-prioridad-sobre-v-punto",
        ),
        pytest.param(
            # Caso real (2026-08-30): Shopify mete el "Vol.NN" en el
            # variant_title, no en el nombre principal -- Sleeves es la
            # única família que también mira variant_title para el código
            # (el resto de famílias solo miran `name`).
            "One Piece Card Game - Official Sleeves", "Card Sleeves Monkey D. Luffy Vol.01 One Piece Card Game",
            "SLEEVES", "VOL01", "EN", None, None,
            id="sleeves-vol-en-variant-title-no-en-el-nombre",
        ),
        pytest.param(
            # "Vol." en el name gana sobre lo que haya en variant_title si
            # ambos aplicasen (name comprobado primero dentro de `combined`).
            "Official Sleeves Vol.02", "Vol.05",
            "SLEEVES", "VOL02", "EN", None, None,
            id="sleeves-vol-en-name-tiene-prioridad-sobre-vol-en-variant",
        ),
        pytest.param(
            # Sin número ni "Vol.", fallback a la tabla de personaje/tema
            # (aportada por el usuario) de la línea "Official Sleeves 1-16".
            "One Piece Card Game: Official Sleeves – Shanks", None,
            "SLEEVES", "VOL10", "EN", None, None,
            id="sleeves-fallback-por-personaje-unico-shanks",
        ),
        pytest.param(
            # "Nami" se repite en varios volúmenes de la línea -- fuera de
            # la tabla a propósito, mejor needs_review que un match falso.
            "One Piece Card Game: Official Sleeves – Nami", None,
            "SLEEVES", None, "EN", None, None,
            id="sleeves-personaje-ambiguo-repetido-no-tiene-codigo",
        ),
        pytest.param(
            # "Standard Blue" a secas colisiona por substring con la edición
            # especial "Standard Blue Gold" de OTRA línea de fundas -- fuera
            # de la tabla a propósito.
            "Limited Card Sleeve -Standard Blue Gold-", None,
            "SLEEVES", None, "EN", None, None,
            id="sleeves-standard-blue-gold-no-colisiona-con-volumen-numerado",
        ),
        pytest.param(
            None, None,
            "OTROS", None, None, None, None,
            id="name-none-no-revienta",
        ),
        pytest.param(
            "", None,
            "OTROS", None, None, None, None,
            id="name-cadena-vacia",
        ),
        pytest.param(
            # "Mazo de inicio" prueba la keyword de tipo en español; "Buggy"
            # es además una entrada real de _STARTER_DECK_CHARACTER_CODES.
            "Mazo de inicio Buggy", None,
            "STARTER_DECK", "ST25", "EN", None, "sobre",
            id="keyword-en-espanol",
        ),
        pytest.param(
            "One Piece TCG", "Inglés",
            "OTROS", None, "EN", None, None,
            id="idioma-por-fallback-de-variant-title",
        ),
        pytest.param(
            "EN Booster Box OP-16", None,
            "ONE_PIECE", "OP16", "EN", "OP16", "display",
            id="EN-mayuscula-como-codigo-de-idioma-real",
        ),
        pytest.param(
            # El fallback name+variant_title (name_variant en _classify_pass)
            # NO es exclusivo de Sleeves -- se aplica a TODAS las famílias de
            # Fase 2: aquí el código real vive solo en variant_title (patrón
            # Shopify real: nombre genérico + variante con el código del
            # lanzamiento).
            "One Piece Card Game Booster Box", "OP-01",
            "ONE_PIECE", "OP01", "EN", "OP01", "display",
            id="codigo-en-variant-title-no-en-el-nombre-one-piece",
        ),
        pytest.param(
            "Starter Deck", "ST-05",
            "STARTER_DECK", "ST05", "EN", None, "sobre",
            id="codigo-en-variant-title-no-en-el-nombre-starter-deck",
        ),
        pytest.param(
            # `name` sigue ganando sobre variant_title si ambos traen código
            # (name_variant concatena name PRIMERO, .search() se queda con
            # el primer match).
            "One Piece Card Game Booster Box OP-16", "OP-01",
            "ONE_PIECE", "OP16", "EN", "OP16", "display",
            id="codigo-en-name-tiene-prioridad-sobre-variant-title",
        ),
        pytest.param(
            "KID – STARTER DECK ONE PIECE – ST 36", None,
            "STARTER_DECK", "ST36", "EN", None, "sobre",
            id="set-code-con-espacio-en-vez-de-guion-caso-real-arte9",
        ),
        pytest.param(
            # LEARN_DECK fusionado en STARTER_DECK -- "learn together" sigue
            # comprobándose PRIMERO dentro de la rama, un raw_name real
            # ("LEARN TOGETHER DECK SET - STARTER DECKS ONE PIECE") contiene
            # AMBAS keywords a la vez. set_code="LD01" fijo (2026-08-30):
            # único release real de esta línea, ver _classify_pass.
            "LEARN TOGETHER DECK SET – STARTER DECKS ONE PIECE", None,
            "STARTER_DECK", "LD01", "EN", None, "sobre",
            id="learn-together-fusionado-en-starter-deck-pese-a-contener-starter-decks",
        ),
        pytest.param(
            # Código PRB-NN explícito -> PREMIUM_BOOSTER_BOX (no
            # PREMIUM_CARD_COLLECTION, que ni siquiera se comprueba aquí --
            # requiere la keyword "premium card collection" en el texto).
            "ONE PIECE CARD GAME - THE BEST VOL2 - PRB02 - EN", None,
            "PREMIUM_BOOSTER_BOX", "PRB02", "EN", None, "sobre",
            id="prefijo-generico-VOL-no-es-set-code-solo-prefijos-de-la-lista-blanca",
        ),
        pytest.param(
            "ONE PIECE CARD GAME - ILLUSTRATION BOX IB-06 - EN", None,
            "ILLUSTRATION_BOX", "VOL06", "EN", None, None,
            id="prefijo-IB-se-reconoce-como-volumen-de-illustration-box",
        ),
        pytest.param(
            # main_set=None (NUEVO, cambio de diseño deliberado): main_set
            # solo se deriva para ONE_PIECE (=set_code) y DOUBLE_PACK (tabla
            # DP<->OP) -- un "OP13" decorativo de acompañamiento en el
            # nombre de un Illustration Box ya no se usa como main_set (era
            # ruido, no señal real -- nunca se compara en el matching).
            "One Piece Illustration Box Vol.6 Law & Rosinante OP13", None,
            "ILLUSTRATION_BOX", "VOL06", "EN", None, None,
            id="illustration-box-usa-vol-main-set-no-se-deriva-del-op-decorativo",
        ),
        pytest.param(
            "One Piece Playmat Limited Edition Vol.1 + 3 Sobres OP-15", None,
            "PLAYMAT", "VOL01", "EN", None, None,
            id="playmat-usa-vol-main-set-no-se-deriva-del-op-decorativo",
        ),
        pytest.param(
            "Official Playmat Limited Edition Vol.3 EN", None,
            "PLAYMAT", "VOL03", "EN", None, None,
            id="playmat-vol-de-un-solo-digito-se-normaliza-con-cero",
        ),
        pytest.param(
            "One Piece | OP-11 A Fist of Divine Speed", "Sobre Inglés",
            "ONE_PIECE", "OP11", "EN", "OP11", "sobre",
            id="tipo-solo-en-variant-nunca-en-name-caso-real-pokemillon-sobre",
        ),
        pytest.param(
            "One Piece | OP-11 A Fist of Divine Speed", "Caja 24 Sobres Inglés",
            "ONE_PIECE", "OP11", "EN", "OP11", "display",
            id="tipo-solo-en-variant-nunca-en-name-caso-real-pokemillon-caja",
        ),
        pytest.param(
            "Mazo ST-22 Ace & Shirohige", None,
            "STARTER_DECK", "ST22", "EN", None, "sobre",
            id="mazo-a-secas-sin-de-inicio-no-colisiona-con-amazon",
        ),
        pytest.param(
            "ONE PIECE TCG - DOBLE PACK SET VOL. 14 (DP-14)", "",
            "DOUBLE_PACK", "DP14", "EN", None, "sobre",
            id="doble-pack-variante-en-espanol",
        ),
        pytest.param(
            "Pack 5 Sobres One Piece Adventure on KAMI’s Island OP15 - Japones", "Default Title",
            "ONE_PIECE", "OP15", "JP", "OP15", "sobre",
            id="sobres-plural-catch-all-no-hace-falta-aqui-el-codigo-op-ya-resuelve",
        ),
        pytest.param(
            "Rebecca (OP10-058) (V.1) Royal Blood (Non-English)", "CGC 10",
            "LOTE_CARTAS", "OP10", None, None, None,
            id="cgc-carta-individual-gradeada-gana-a-cualquier-otra-keyword",
        ),
        pytest.param(
            "Monkey.D.Luffy (P-001) 7 - Eleven Promos", "PSA 10",
            "LOTE_CARTAS", None, "EN", None, None,
            id="psa-carta-individual-gradeada",
        ),
        pytest.param(
            "ONE PIECE TCG - EB-05", "",
            "EXTRA_BOOSTER", "EB05", "EN", None, "sobre",
            id="codigo-eb-sin-ninguna-palabra-de-tipo-alrededor-family-propia",
        ),
        pytest.param(
            "(CASE) THE BEST 2 – PRB-02 – x10 Booster Box- One Piece Card Game", None,
            "PREMIUM_BOOSTER_BOX", "PRB02", "EN", None, "case",
            id="premium-booster-box-parentesis-case",
        ),
        pytest.param(
            # NUEVO: "case" gana como packaging (no como tipo, ya no hay
            # família BOOSTER_CASE aparte) -- ONE_PIECE por el código OP-16.
            "Case - Booster Box OP-16 The Time of Battle", None,
            "ONE_PIECE", "OP16", "EN", "OP16", "case",
            id="case-como-packaging-no-como-tipo-aparte",
        ),
        pytest.param(
            # Reclasificación deliberada del rediseño (Corrección 4): el
            # código ST-05 activa la família STARTER_DECK directamente --
            # ya no hay una família BOOSTER_CASE genérica que gane primero
            # y arrastre un set_code de otra família como decorativo.
            "One Piece Card Game Booster Box Case ST-05", None,
            "STARTER_DECK", "ST05", "EN", None, "case",
            id="codigo-st-activa-starter-deck-directamente-reclasificacion-del-rediseno",
        ),
        pytest.param(
            # docs/pendientes-motor-matching.md punto 2 -- caso real: un
            # accesorio ("Card Case", una funda de cartas) NO debe colar
            # como ningún tipo de producto sellado -- sin código de família
            # ni título reconocible ni keyword de sellado, cae en OTROS
            # (ya no hace falta un _BOOSTER_CASE_CONTEXT_RE dedicado: al no
            # activarse ninguna família, nunca se llega a evaluar packaging).
            "Limited Card Case -Monkey.D.Luffy-", None,
            "OTROS", None, "EN", None, None,
            id="card-case-accesorio-no-activa-ninguna-familia",
        ),
        pytest.param(
            "[PREORDER] [INGLÉS] One Piece Card Game OP-19 Case", None,
            "ONE_PIECE", "OP19", "EN", "OP19", "case",
            id="case-sin-booster-ni-box-pegado-confirma-por-codigo-de-set",
        ),
        pytest.param(
            "Case OP02 Paramount War (12 cajas)", None,
            "ONE_PIECE", "OP02", "EN", "OP02", "case",
            id="case-con-contexto-cajas-en-espanol",
        ),
        pytest.param(
            "One Piece TCG Official Dice and Dice Case Set", None,
            "DICE_ACCESSORY", None, "EN", None, None,
            id="dice-case-no-es-ningun-booster",
        ),
        pytest.param(
            "One Piece Card Game - Playmat and Card Case Set -25th Edition-", None,
            "PLAYMAT", None, "EN", None, None,
            id="playmat-y-card-case-gana-playmat-por-orden-de-familia",
        ),
        pytest.param(
            "Funko Pop! One Piece - Case 5+1 Jewelry Bonney", None,
            "OTROS", None, "EN", None, None,
            id="case-sin-contexto-ni-codigo-no-activa-ninguna-familia",
        ),
        pytest.param(
            "Caja One Piece Devil Fruits Collection Vol.2 - Ingles", None,
            "DEVIL_FRUITS_COLLECTION", "DF02", "EN", None, None,
            id="devil-fruits-collection-sin-codigo-df-explicito-usa-vol-como-fallback",
        ),
        pytest.param(
            # "Caja" activa packaging='display' dentro de STARTER_DECK
            # (fusionado desde LEARN_DECK) -- ya no colisiona con ONE_PIECE
            # porque "learn together"/"aprende a jugar" se comprueba ANTES.
            "One Piece | Caja Aprende a Jugar", None,
            "STARTER_DECK", "LD01", "EN", None, "display",
            id="starter-deck-aprende-a-jugar-en-espanol-con-caja-como-packaging",
        ),
        pytest.param(
            # docs/pendientes-motor-matching.md punto 4 -- la carta suelta
            # de regalo DON!! (viene DENTRO de Double Pack Set Vol.10,
            # vendida por separado) no debe confirmar contra el Double Pack
            # sellado completo. Sin "set"/"pack"/"caja"/"box"/"sobre" que
            # confirme que es el sellado -> PROMO_CARD (Fase 0).
            "Don!! (DP10 Map) - One Piece Products (DON!!)", "English / Near Mint / Normal",
            "PROMO_CARD", "DP10", "EN", None, None,
            id="don-card-suelta-sin-contexto-de-sellado-es-promo-card",
        ),
        pytest.param(
            # Control negativo -- "Pack" en el mismo texto SÍ confirma que
            # es el sellado completo. main_set='OP09' se deriva ahora de la
            # tabla DP<->OP (Corrección 5), no de un "OP09" literal --
            # coincide aquí, pero por una vía distinta a antes.
            "One Piece Special DON!! Card Pack DP-06 - Emperors in the New World (OP09)", "Inglés",
            "DOUBLE_PACK", "DP06", "EN", "OP09", "sobre",
            id="don-card-pack-con-pack-en-el-texto-sigue-siendo-double-pack",
        ),
        pytest.param(
            # main_set='OP15' vía tabla DP<->OP (antes None, ninguna
            # regresión: era un hueco de diseño sin usar, ver Corrección 5).
            "One Piece Card Game Double Pack Set Vol.10 [DP-10] – 2 Booster Packs + Exclusive DON!! Card", None,
            "DOUBLE_PACK", "DP10", "EN", "OP15", "sobre",
            id="double-pack-set-completo-con-don-card-de-regalo-main-set-via-tabla",
        ),
        pytest.param(
            "One Piece Tcg Caja Op04 KINGDOMS OF INTRIGUE", None,
            "ONE_PIECE", "OP04", "EN", "OP04", "display",
            id="set-code-minuscula-op04",
        ),
        pytest.param(
            "One Piece Card Game Memorial Collection Eb-02 Sobre", None,
            "EXTRA_BOOSTER", "EB02", "EN", None, "sobre",
            id="set-code-mixto-eb-02-family-propia-no-generica",
        ),
        pytest.param(
            "One Piece Card Game Zoro and Sanji Starter Deck 12", None,
            "STARTER_DECK", "ST12", "EN", None, "sobre",
            id="starter-deck-numero-suelto-sin-prefijo",
        ),
        pytest.param(
            # main_set='OP12' vía tabla DP<->OP (antes None -- información
            # gratis que ya teníamos y no se usaba, Corrección 5).
            "DOUBLE PACK SET 8 LEGACY OF THE MASTER", None,
            "DOUBLE_PACK", "DP08", "EN", "OP12", "sobre",
            id="double-pack-set-numero-suelto-main-set-via-tabla",
        ),
        pytest.param(
            "One Piece Starter Deck Shanks", None,
            "STARTER_DECK", "ST23", "EN", None, "sobre",
            id="starter-deck-personaje-inequivoco-shanks",
        ),
        pytest.param(
            "One Piece Starter Deck Charlotte Katakuri", None,
            "STARTER_DECK", None, "EN", None, "sobre",
            id="starter-deck-personaje-ambiguo-katakuri-no-resuelve",
        ),
        pytest.param(
            "One Piece Starter Deck Ace &amp; Newgate", None,
            "STARTER_DECK", "ST22", "EN", None, "sobre",
            id="starter-deck-personaje-con-entidad-html-ace-newgate",
        ),
        pytest.param(
            # Tabla de títulos propia de ONE_PIECE (_OP_TITLE_CODES) --
            # título oficial de release sin código.
            "One Piece Romance Dawn Booster Box", None,
            "ONE_PIECE", "OP01", "EN", "OP01", "display",
            id="release-title-sin-codigo-romance-dawn",
        ),
        pytest.param(
            "One Piece Starter Deck Ultra Deck The Three Captains", None,
            "STARTER_DECK", "ST10", "EN", None, "sobre",
            id="starter-deck-frase-multi-palabra-three-captains",
        ),
        pytest.param(
            "One Piece Tcg Premium2 – PRB-02 sobre", None,
            "PREMIUM_BOOSTER_BOX", "PRB02", "EN", None, "sobre",
            id="codigo-prb-activa-premium-booster-box-directamente",
        ),
        pytest.param(
            "ONE PIECE CARD GAME PREMIUM CARD COLLECTION VOL 3", None,
            "PREMIUM_CARD_COLLECTION", "VOL03", "EN", None, None,
            id="premium-card-collection-vol-sin-code-real",
        ),
        pytest.param(
            "One Piece | Fruta del Diablo vol.1 [DF-01] Inglés 2023", None,
            "DEVIL_FRUITS_COLLECTION", "DF01", "EN", None, None,
            id="devil-fruits-collection-espanol",
        ),
        pytest.param(
            # main_set='OP14' vía tabla DP<->OP -- NO el "OP12" decorativo
            # que aparece en el propio texto (Corrección 5: "nunca al
            # revés", el DP-NN real manda, no un OP mencionado al lado).
            # DP09->OP14 verificado 2026-08-30 contra release_date del
            # catálogo oficial (antes la tabla tenía DP09->OP13 por error).
            "One Piece DP09 The Azure Sea's Seven OP12", None,
            "DOUBLE_PACK", "DP09", "EN", "OP14", "sobre",
            id="codigo-dp-activa-double-pack-directamente-main-set-por-tabla-no-por-texto",
        ),
        pytest.param(
            # Bug real corregido 2026-08-30: _RANGO_CODIGOS_RE exigía el
            # MISMO prefijo en ambos lados (backreference) para no confundir
            # un designador DOBLE real ("OP15-EB04", un único lanzamiento
            # con dos códigos) con un rango ("ST-15 - ST-20"). Sin código DP
            # ni "double pack" en el texto -> ONE_PIECE gana, set_code=OP15.
            "ONE PIECE OP15 - EB04 - ADVENTURE ON THE ISLAND OF THE GODS - Caja de Sobres", None,
            "ONE_PIECE", "OP15", "EN", "OP15", "display",
            id="op-eb-designador-doble-no-es-un-rango-cuenta-como-op",
        ),
        pytest.param(
            # Mismo bug, lado Double Pack: "DOUBLE PACK" en el texto activa
            # esa família ANTES de llegar a ONE_PIECE, así que el designador
            # doble DP09-OP12 (con un OP decorativo, posiblemente erróneo de
            # la propia tienda) siempre cuenta como Double Pack, nunca OP.
            "ONE PIECE DOUBLE PACK DP09 - OP12 LEGACY OF THE MASTER", None,
            "DOUBLE_PACK", "DP09", "EN", "OP14", "sobre",
            id="dp-op-designador-doble-siempre-cuenta-como-double-pack",
        ),
        pytest.param(
            # _OP_CODE_RE ahora acepta espacio como separador, igual que ST
            # desde el fix de "ST 36" (Arte9) -- antes solo aceptaba guion u
            # nada, "OP 12" con espacio se quedaba sin set_code.
            "One Piece - OP 12  - Booster Box (24 packs)", None,
            "ONE_PIECE", "OP12", "EN", "OP12", "display",
            id="op-con-espacio-como-separador",
        ),
        pytest.param(
            # Accesorio de marca ajena (deck box/funda de terceros, nunca un
            # producto de Bandai) -- "caja" ya no basta para asumir
            # ONE_PIECE si el texto es claramente de un accesorio de otra
            # marca. Cae a OTROS (not_applicable en matcher.py).
            "Caja Ultimate Guard Deck Box Sidewinder 80+ One Piece - Zoro", None,
            "OTROS", None, "EN", None, None,
            id="accesorio-ultimate-guard-no-cuenta-como-one-piece",
        ),
        pytest.param(
            "Caja de Almacenamiento Pikachu", None,
            "OTROS", None, "EN", None, None,
            id="caja-de-almacenamiento-generica-no-cuenta-como-one-piece",
        ),
        pytest.param(
            # "MAZO ONE PIECE NN" -- equivalente en español de "Starter Deck
            # NN" (_STARTER_DECK_NUM_RE), mismo patrón, misma keyword "mazo"
            # que ya activa la família.
            "MAZO ONE PIECE 08", None,
            "STARTER_DECK", "ST08", "EN", None, "sobre",
            id="mazo-one-piece-numero-suelto-en-espanol",
        ),
        pytest.param(
            # Learn Together ahora lleva código fijo "LD01" (único release
            # real de esta línea) en vez de quedar siempre sin set_code.
            "One Piece Card Game: Learn Together Deck Set", None,
            "STARTER_DECK", "LD01", "EN", None, "sobre",
            id="learn-together-codigo-fijo-ld01-sin-codigo-explicito",
        ),
        pytest.param(
            # Si el texto SÍ trae el código LD explícito, se respeta ese en
            # vez de asumir LD01 a ciegas (por si algún día hay un LD02).
            "One Piece Card Game - Learn Together Deck Set (LD02)", None,
            "STARTER_DECK", "LD02", "EN", None, "sobre",
            id="learn-together-respeta-codigo-ld-explicito-si-aparece",
        ),
        pytest.param(
            # Título del booster asociado (_DP_TITLE_CODES, derivada de
            # _OP_TITLE_CODES + _DP_TO_OP) -- sin ningún código DP-NN visible
            # en el texto, solo el título oficial del lanzamiento.
            "One Piece The World's Strongest Warriors Double Pack", None,
            "DOUBLE_PACK", "DP12", "EN", "OP17", "sobre",
            id="double-pack-por-titulo-del-booster-sin-codigo-visible",
        ),
        pytest.param(
            # Tabla estricta de Premium Card Collection falla ("LIVE ACTION"
            # sin la palabra "Edition") -> fallback de clave acortada.
            "ONE PIECE CARD GAME - PREMIUM CARD COLLECTION - LIVE ACTION", None,
            "PREMIUM_CARD_COLLECTION", "EDLIVEACTION", "EN", None, None,
            id="premium-card-collection-fallback-clave-acortada-live-action",
        ),
        pytest.param(
            "ONE PIECE TCG PREMIUM CARD COLLECTION -25TH-", None,
            "PREMIUM_CARD_COLLECTION", "ED25TH", "EN", None, None,
            id="premium-card-collection-fallback-clave-acortada-25th",
        ),
        pytest.param(
            # Case por multiplicador x12 (sin la palabra "case") -- ONE_PIECE
            # vía código OP-16, packaging='case' por _qty_matches.
            "[INGLÉS] One Piece Card Game OP-16 Caja de sobres x12", None,
            "ONE_PIECE", "OP16", "EN", "OP16", "case",
            id="packaging-case-por-multiplicador-x12-sin-palabra-case",
        ),
        pytest.param(
            # Control negativo -- x12 no es la cantidad de display (6) ni de
            # case (STARTER_DECK no tiene case en _PACKAGING_UNITS) -> sobre.
            "One Piece Card Game Starter Deck EX Gear5 [ST21] x12", None,
            "STARTER_DECK", "ST21", "EN", None, "sobre",
            id="x12-no-dispara-packaging-case-fuera-de-one-piece-extra-booster-premium",
        ),
        pytest.param(
            "One Piece Card Game Playmat And Storage Box Shanks", None,
            "PLAYMAT", "SHANKS", "EN", None, None,
            id="playmat-personaje-shanks",
        ),
        pytest.param(
            "One Piece Card Game Playmat And Storage Box Porgas D. Ace", None,
            "PLAYMAT", "ACE", "EN", None, None,
            id="playmat-personaje-typo-porgas-ace",
        ),
        pytest.param(
            # Reclasificación deliberada del rediseño: DEVIL_FRUITS_COLLECTION
            # es la PRIMERA família de Fase 2 -- un código DF-NN inequívoco
            # activa su família propia sin importar qué otra keyword
            # genérica ("Booster Box") también aparezca en el texto. Ya no
            # existe el gating "solo si name+variant quedan en OTROS" del
            # sistema anterior -- cada família se activa por SU PROPIA señal,
            # sin depender de que las demás hayan fallado antes.
            "Booster Box Something DF-01", None,
            "DEVIL_FRUITS_COLLECTION", "DF01", "EN", None, None,
            id="df-code-activa-su-familia-sin-depender-de-otras-keywords-reclasificacion-del-rediseno",
        ),
    ]

    @pytest.mark.parametrize("name,variant_title,product_type,set_code,language,main_set,packaging", CASES)
    def test_classification_table(self, name, variant_title, product_type, set_code, language, main_set, packaging):
        result = classify_product(name, variant_title)
        assert result.product_type == product_type
        assert result.set_code == set_code
        assert result.language == language
        assert result.main_set == main_set
        assert result.packaging == packaging


class TestCatchAllGenerico:
    """Corrección 10 (decisión de producto 2026-08-30, cierra §11.4) --
    producto sellado sin código ni título reconocible de ninguna família
    anterior, pero con keyword de sellado genérica ("booster"/"caja"/
    "sobre(s)") -> se asume ONE_PIECE (el booster principal), mismo
    comportamiento que el sistema anterior. 8 casos reales de producción
    documentados en la propuesta."""

    @pytest.mark.parametrize("raw_name,esperado_packaging", [
        ("Booster Box EN", "display"),
        ("Caja 24 Sobres Inglés", "display"),
        ("One Piece Booster Box Castellano", "display"),
        ("Sobre Inglés", "sobre"),
    ])
    def test_catch_all_resuelve_a_one_piece_sin_codigo(self, raw_name, esperado_packaging):
        c = classify_product(raw_name)
        assert c.product_type == "ONE_PIECE"
        assert c.set_code is None
        assert c.main_set is None
        assert c.packaging == esperado_packaging

    def test_catch_all_no_confunde_accesorios_de_almacenaje_con_booster(self):
        # Encontrado al validar la implementación contra el catálogo oficial
        # real: "Official Storage Box" NO es un booster sellado -- el
        # catch-all no debe reconocer "box" suelto (solo lo reconocía el
        # sistema anterior como parte de la FRASE "booster box", nunca a
        # secas). Sin esta protección, todos los accesorios de
        # almacenamiento/fundas/binders que mencionan "box" colarían como
        # ONE_PIECE.
        c = classify_product("Official Storage Box")
        assert c.product_type == "OTROS"

    def test_catch_all_no_dispara_dentro_de_otra_familia_ya_resuelta(self):
        # "Playmat and Storage Box" -- PLAYMAT se resuelve antes (keyword
        # propia), el catch-all nunca llega a evaluarse.
        c = classify_product("Playmat and Storage Box Set -Shanks-")
        assert c.product_type == "PLAYMAT"


class TestCorreccionCartaIndividualNoActivaFamilia:
    """Corrección 8 -- las seis regex de código de família de Fase 2 llevan
    `(?!-\\d)`: un código de família que en realidad es el prefijo de la
    numeración de una CARTA INDIVIDUAL dentro de otro producto no debe
    activar esa família como si fuera el producto sellado completo.
    Encontrada al contrastar el prototipo del documento contra la suite
    real, no solo contra nombres sueltos -- ver docs/propuestas/
    guia_nuevo_matcher.md §10.1, cuarta ronda."""

    def test_codigo_eb_de_carta_individual_no_activa_extra_booster(self):
        c = classify_product("ONE PIECE CHOPPER’s Vol. 1 carta/comic promocional EB02-003", "")
        assert c.product_type == "OTROS"
        # _GENERIC_CODE_RE (sin el lookahead, Fase 0/catch-all) sigue
        # capturando el set de acompañamiento -- información barata, igual
        # que ya hace LOTE_CARTAS/PROMO_CARD.
        assert c.set_code == "EB02"

    def test_codigo_de_carta_individual_en_lote_cartas_no_se_ve_afectado(self):
        # _GENERIC_CODE_RE (Fase 0) NO lleva el lookahead a propósito --
        # necesita seguir siendo permisiva para el set de acompañamiento de
        # una carta suelta gradeada.
        c = classify_product("Rebecca (OP10-058) (V.1) Royal Blood (Non-English)", "CGC 10")
        assert c.product_type == "LOTE_CARTAS"
        assert c.set_code == "OP10"


class TestCorreccionRangoDeCodigosEnFase2:
    """Corrección 9 -- guarda de rango (`_RANGO_CODIGOS_RE`, portada del
    `classify.py` anterior sin cambios) dentro de las funciones de
    extracción de Fase 2: un rango de códigos ("ST-15 - ST-20",
    "[ST-31]~[ST-36]") describe un pack ligado a TODO un lote de mazos, no a
    uno solo -- extraer el primer extremo sería un código inventado. La
    família igual se activa (por el código presente), solo el set_code
    queda en None."""

    @pytest.mark.parametrize("raw_name", [
        "One Piece Sobre ST-15 - ST-20 Release Event Pack",
        "One Piece Sobre Beginners Deck Party ST-31 - ST-36 Participation Pack",
        "One Piece Sobre Beginners Deck Party [ST-31]~[ST-36] Winner pack",
    ])
    def test_rango_de_codigos_no_extrae_el_primer_extremo(self, raw_name):
        c = classify_product(raw_name)
        assert c.product_type == "STARTER_DECK"
        assert c.set_code is None

    def test_codigo_de_carta_individual_no_se_confunde_con_rango(self):
        c = classify_product("Rebecca (OP10-058) (V.1) Royal Blood (Non-English)", "CGC 10")
        assert c.set_code == "OP10"

    def test_codigo_eb_de_carta_individual_no_se_confunde_con_rango(self):
        c = classify_product("ONE PIECE CHOPPER’s Vol. 1 carta/comic promocional EB02-003", "")
        assert c.set_code == "EB02"


class TestExtraTypeHintDeTags:
    """extra_type_hint: señal estructurada adicional -- de momento,
    `store_product.raw_tags` de Shopify. Verificado en vivo contra
    Pokemillon real: su `product_type` nativo de Shopify viene siempre
    vacío, pero `tags` sí trae señal fiable, de ahí usar esto."""

    def test_tags_resuelve_tipo_que_ni_name_ni_variant_traen(self):
        # Caso real de Pokemillon: ni `name` ni `variant` mencionan
        # "caja"/"booster"/etc -- solo `tags` lo dice.
        result = classify_product(
            "One Piece OP13 Carrying On His Will", "Inglés",
            "Caja One Piece, Cajas, Cajas de Sobres, One Piece, OP-13 Carrying On His Will",
        )
        assert result.product_type == "ONE_PIECE"
        assert result.set_code == "OP13"
        assert result.language == "EN"

    def test_sin_tags_ya_no_hace_falta_para_este_caso(self):
        # A diferencia del sistema anterior (donde este caso SÍ necesitaba
        # tags), el código OP13 explícito en el propio nombre ya resuelve
        # ONE_PIECE en la primera pasada -- la Corrección de família-por-
        # código-propio hace innecesaria la segunda pasada aquí (ver §4.5
        # de la propuesta).
        result = classify_product("One Piece OP13 Carrying On His Will", "Inglés")
        assert result.product_type == "ONE_PIECE"
        assert result.set_code == "OP13"

    def test_tags_none_no_revienta(self):
        result = classify_product("One Piece OP13 Carrying On His Will", "Inglés", None)
        assert result.product_type == "ONE_PIECE"

    def test_tags_no_pisa_una_palabra_de_tipo_ya_clara_en_name(self):
        result = classify_product(
            "Starter Deck ST-21 Gear 5 Inglés", None, "Cajas, Cajas de Sobres",
        )
        assert result.product_type == "STARTER_DECK"

    def test_codigo_dp_gana_a_tag_generico_de_caja(self):
        result = classify_product(
            "One Piece | DP-07 A Fist of Divine Speed OP-11", "Inglés",
            "Caja One Piece, Cajas, One Piece, OP-11 A Fist of Divine Speed",
        )
        assert result.product_type == "DOUBLE_PACK"
        assert result.set_code == "DP07"

    def test_tags_reciclada_no_pisa_una_regla_que_ya_resuelve_por_name(self):
        # `raw_tags` es metadato de catálogo reciclado ENTRE PRODUCTOS SIN
        # RELACIÓN -- este Illustration Box tenía la tag "PRB-02 The Best
        # vol. 2" (de otro producto), pero PREMIUM_BOOSTER_BOX exige código/
        # título PROPIO (tabla exclusiva, Corrección 4) -- ya no puede
        # colar solo por tener una entrada que casualmente aparece en el
        # texto combinado.
        result = classify_product(
            "One Piece Illustration Box Vol.6 Law & Rosinante OP13", "Inglés",
            "Caja One Piece, Cajas, One Piece, OP-13 Carrying On His Will, PRB-02 The Best vol. 2",
        )
        assert result.product_type == "ILLUSTRATION_BOX"
        assert result.set_code == "VOL06"

    def test_tags_reciclada_no_pisa_devil_fruits_collection(self):
        result = classify_product(
            "One Piece | Fruta del Diablo Mera Mera vol. 2 [DF-02]", "Inglés",
            "Caja One Piece, Cajas, Devil Fruit, One Piece",
        )
        assert result.product_type == "DEVIL_FRUITS_COLLECTION"
        assert result.set_code == "DF02"

    def test_tags_sigue_siendo_respaldo_valido_para_el_catch_all_generico(self):
        # Caso donde name+variant NO traen ni código ni keyword de sellado
        # en absoluto -- ahí las tags siguen aportando valor real (§4.5).
        result = classify_product(
            "One Piece Something Without Any Keyword At All", "Default",
            "Caja One Piece, Cajas, Cajas de Sobres",
        )
        assert result.product_type == "ONE_PIECE"

    def test_codigo_dp_en_name_pisa_tag_reciclada_de_sobre(self):
        result = classify_product(
            "One Piece DP09 The Azure Sea's Seven OP14", "Inglés",
            "One Piece, OP-14 The Azure Sea's Seven, Sobre One Piece, Sobres",
        )
        assert result.product_type == "DOUBLE_PACK"
        assert result.set_code == "DP09"

    def test_classify_with_category_tambien_acepta_el_tercer_argumento(self):
        classification, category_slug = classify_with_category(
            "One Piece OP13 Carrying On His Will", "Inglés",
            "Caja One Piece, Cajas, Cajas de Sobres, One Piece, OP-13 Carrying On His Will",
        )
        assert classification.product_type == "ONE_PIECE"
        assert category_slug == "one-piece"


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
        assert _detect_language("Entrada genérica sin idioma") is None


class TestJPSueltoAdemasDePalabraCompleta:
    """9 filas reales usan el código suelto "JP" (ej. "...One Piece Tcg
    [JP]", "...JP Edition") sin la palabra completa "japonés"/"japones" --
    \\bJP\\b sobre el texto original, mismo patrón que ya tenía \\bEN\\b
    para inglés."""

    def test_jp_suelto_entre_corchetes_caso_real(self):
        assert _detect_language("Sobre Booster OP-14: The Azure Sea's Seven - One Piece Tcg [JP]") == "JP"

    def test_palabra_completa_japones_sigue_funcionando(self):
        assert _detect_language("One Piece TCG OP16 Booster Box (Japones)") == "JP"

    def test_jp_minuscula_dentro_de_otra_palabra_no_matchea(self):
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


class TestMainSetSoloParaOnePieceYDoublePack:
    """main_set (NUEVO, cambio de diseño deliberado respecto al sistema
    anterior): antes se derivaba de un "OP\\d+" literal buscado en TODO
    nombre, sin importar el product_type -- ruido para família sin relación
    real con OP (Illustration Box, Playmat...), nunca usado por el
    matching. Ahora solo se deriva para ONE_PIECE (=set_code) y DOUBLE_PACK
    (tabla DP<->OP, Corrección 5) -- cualquier otra família siempre da
    main_set=None."""

    def test_one_piece_dos_codigos_en_el_texto_primera_coincidencia_gana(self):
        result = classify_product("OP16 y OP17 en el mismo título")
        assert result.product_type == "ONE_PIECE"
        assert result.set_code == "OP16"
        assert result.main_set == "OP16"

    def test_op_de_tres_digitos_si_matchea_ahora_mismo_limite_que_set_code(self):
        # A diferencia del sistema anterior (main_set limitado a 1-2
        # dígitos por una regex propia distinta de la de set_code), ahora
        # main_set = set_code para ONE_PIECE -- mismo límite (\\d{1,3}) en
        # los dos, sin asimetría.
        result = classify_product("Booster Box OP-100")
        assert result.set_code == "OP100"
        assert result.main_set == "OP100"

    def test_starter_deck_nunca_deriva_main_set(self):
        result = classify_product("Starter Deck ST-100")
        assert result.set_code == "ST100"
        assert result.main_set is None

    def test_playmat_nunca_deriva_main_set_aunque_mencione_op(self):
        result = classify_product("One Piece Playmat Limited Edition Vol.1 + 3 Sobres OP-15")
        assert result.main_set is None

    def test_preposicion_en_no_se_confunde_con_idioma_en_este_texto(self):
        assert _detect_language("OP16 y OP17 en el mismo título") is None


class TestDoublePackPrefiereCodigoDP:
    """Un Double Pack a menudo menciona el set OP-NN que acompaña como
    contexto ANTES del código DP-NN real -- el prefijo genérico se quedaba
    con el primero que aparece en el texto (izquierda a derecha), no con el
    DP real. main_set se deriva SIEMPRE de la tabla DP<->OP, nunca del OP
    decorativo del propio texto (Corrección 5)."""

    def test_op_antes_que_dp_en_el_texto_se_queda_con_dp(self):
        result = classify_product("One Piece | OP15 Double Pack Set DP-10 Adventure on KAMI's Island")
        assert result.product_type == "DOUBLE_PACK"
        assert result.set_code == "DP10"
        assert result.main_set == "OP15"  # DP10 -> OP15 en _DP_TO_OP, coincide con el decorativo aquí

    def test_vol_y_op_y_dp_juntos_se_queda_con_dp(self):
        result = classify_product("DOUBLE PACK SET VOL.08 OP-12 DP-08 LEGACY OF THE MASTER ONE PIECE TCG")
        assert result.set_code == "DP08"
        assert result.main_set == "OP12"  # DP08 -> OP12 en _DP_TO_OP

    def test_sin_codigo_dp_en_el_texto_set_code_es_none(self):
        result = classify_product("One Piece Card Game Double Pack Set OP-14")
        assert result.set_code is None
        assert result.main_set is None


class TestIllustrationBoxCodigoIB:
    """Distrito Zero/Gameria etiquetan Illustration Box como "IB-NN", sin la
    palabra "Vol" en ningún sitio -- _VOLUME_RE (solo "Vol.N") dejaba
    set_code=None."""

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
    cada uno por su lado."""

    def test_product_type_con_categoria_sembrada_devuelve_su_slug(self):
        classification, category_slug = classify_with_category("Booster Box OP-16")
        assert classification.product_type == "ONE_PIECE"
        assert category_slug == "one-piece"

    def test_not_applicable_product_type_devuelve_slug_none(self):
        classification, category_slug = classify_with_category("Lote 50 cartas sueltas")
        assert classification.product_type == "LOTE_CARTAS"
        assert category_slug is None

    @pytest.mark.parametrize("raw_name,expected_type", [
        ("Mystery Pack One Piece", "MYSTERY_PACK"),
        ("One Piece Dice Set", "DICE_ACCESSORY"),
        ("One Piece Promo Card OP-01", "PROMO_CARD"),
    ])
    def test_familias_de_fase_0_nunca_tienen_categoria(self, raw_name, expected_type):
        # PROMO_CARD/MYSTERY_PACK/DICE_ACCESSORY suben a Fase 0 (NUEVO,
        # cierra §2 de la propuesta) -- ya no tienen categoría sembrada, a
        # diferencia del sistema anterior.
        classification, category_slug = classify_with_category(raw_name)
        assert classification.product_type == expected_type
        assert category_slug is None

    def test_variant_title_se_propaga_igual_que_en_classify_product(self):
        classification, _ = classify_with_category("One Piece TCG", "Inglés")
        assert classification.language == "EN"


class TestDFCodePorPatronDeCodigo:
    """DF-NN identifica Devil Fruits Collection sin depender de la keyword
    en inglés ("devil fruits collection"), verificado como prefijo
    exclusivo en las 194 líneas del catálogo oficial completo. Es la
    PRIMERA família comprobada en Fase 2 -- se activa por su propio código,
    sin depender de que ninguna otra família haya fallado antes (a
    diferencia del sistema anterior, donde este fallback solo se probaba si
    name+variant se quedaban en OTROS)."""

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
        c = classify_product(
            "One Piece | DF03 Fruta del Diablo Ope Ope No Mi vol. 3  OP12", "Inglés",
            "Black Friday, Caja One Piece, Cajas, Devil Fruit, One Piece",
        )
        assert c.product_type == "DEVIL_FRUITS_COLLECTION"
        assert c.set_code == "DF03"

    def test_df_code_gana_a_keyword_generica_de_otra_familia_reclasificacion_del_rediseno(self):
        # Ver TestClassifyProductTable id=df-code-activa-su-familia-...
        # -- documentado aquí también porque es la contraparte directa del
        # test que en el sistema anterior fijaba justo el comportamiento
        # opuesto (BOOSTER_BOX ganaba al código DF-NN).
        c = classify_product("Booster Box Something DF-01", None)
        assert c.product_type == "DEVIL_FRUITS_COLLECTION"

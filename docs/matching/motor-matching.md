# Motor de matching — consolidado

> **⚠️ SUPERSEDIDO (2026-08-30).** Este documento describe el sistema de
> clasificación/matching **anterior al Recognition Pipeline**
> (`CLASSIFICATION_RULES`, `BOOSTER_BOX`/`BOOSTER_PACK`/`BOOSTER_CASE` como
> categorías separadas, `PREMIUM_COLLECTION` única, `LEARN_DECK` aparte,
> `_evaluate()` sin Evidence Builder/Decision Policy). Ese diseño fue
> reemplazado por completo: ver `docs/propuestas/guia_nuevo_matcher.md` (el
> diseño, con las Correcciones 1-10 acordadas) y el código real en
> `shared/shared/classify.py`/`store_monitor/matcher.py`. Los cambios
> principales respecto a todo lo que sigue en este documento:
>
> - `BOOSTER_BOX`/`BOOSTER_PACK`/`BOOSTER_CASE` se funden en `ONE_PIECE`,
>   distinguidas por el campo `Classification.packaging`
>   (`"sobre"`/`"display"`/`"case"`), no por categoría ni por
>   `is_box_variant()`/`_BOOSTER_CASE_RE`.
> - `PREMIUM_COLLECTION` se separa en `PREMIUM_CARD_COLLECTION` y
>   `PREMIUM_BOOSTER_BOX` (productos distintos, no una variante de
>   empaquetado del mismo).
> - `LEARN_DECK` se funde en `STARTER_DECK`. `EXTRA_BOOSTER` y `SLEEVES` son
>   família nuevas.
> - `PROMO_CARD`/`MYSTERY_PACK`/`DICE_ACCESSORY` suben a Fase 0 del pipeline
>   (`not_applicable` siempre, igual que `LOTE_CARTAS`) -- ya no tienen
>   categoría propia ni entran nunca en `_best_candidate()`.
> - `_evaluate()` se reestructuró en Evidence Builder (`build_evidence()`) +
>   Decision Policy (`decide()`), con un camino nuevo de coincidencia EXACTA
>   de nombre (`exact_name_match`) que no existía en el diseño de abajo.
>
> Se conserva el resto del documento tal cual (registro histórico de cómo
> se llegó hasta el sistema anterior, con sus fechas y commits reales) --
> no se ha reescrito retroactivamente el `classify.py`/`matcher.py` citados
> en los fragmentos de código de abajo, que ya no reflejan el código real.

Une los tres documentos de trabajo sobre el motor de matching (`classify.py`/`matcher.py`/`seed_official_catalog.py`), en el orden en que se fueron escribiendo: primero la implementación de base del auto-confirmado por `set_code`, después la sesión de revisión de datos reales que propuso mejoras concretas, y por último la lista viva de pendientes (que incluye la auditoría más reciente, `needs_review` 182→49).

Fusionado el 2026-08-29 a partir de tres ficheros que antes vivían sueltos en `docs/`: `implementacion-auto-confirmado-setcode.md`, `propuesta-mejoras-matching-sesion.md` y `pendientes-motor-matching.md`. Las referencias que tenían los originales a documentos que no existen en el repo se han quitado.

---

# Parte 1 — Implementación: auto-confirmado por `set_code` exacto

## Implementación — Auto-confirmado por `set_code` exacto

Especifica los cambios de código necesarios para lo acordado tras revisar a mano 20 casos reales del CSV, y el set de pruebas centrado en **falsos positivos** — casos reales que comparten `set_code` pero que el motor debe seguir sin confirmar solo.

**Hecho (2026-08-27):** los 4 cambios de la sección 1 y el set de pruebas de la sección 2 están implementados -- `matcher.py`, `shared/classify.py`, `seed-catalog-app-tcg.sql`, `store_monitor/tests/test_matcher.py` y `store_monitor/tests/test_classify_product.py`. Único punto explícitamente fuera de alcance (ver 1.3): sembrar los canónicos de Case en `seed_official_catalog.py` sigue pendiente.

---

### 1. Cambios a realizar, y por qué cada uno

#### 1.1 `_best_candidate()` debe distinguir candidato primario de candidato por fallback cross-categoría

**Por qué:** el fallback cross-categoría (añadido en `4b11bbb` para el caso PRB02) busca por `set_code` en *todo* el catálogo cuando la categoría derivada no tiene nada — es una señal más débil a propósito, pensada para *sugerir*, no para confirmar sola. Sin distinguirlo, el auto-confirmado por `set_code` trataría igual un candidato de su propia categoría que uno encontrado por casualidad de código en una categoría distinta (caso real: una carta promo individual — categoría `promo-card`, vacía — emparejada contra un Booster Pack completo solo porque el código `OP13` aparece mencionado de forma decorativa en el nombre de la carta).

**Cambio:** `_best_candidate()` devuelve también de qué búsqueda vino el resultado (por ejemplo, una tupla con un booleano `es_fallback`, o dos funciones separadas que el llamador combina explícitamente en vez de una sola función que decide sola).

#### 1.2 `cantidad_es_ambigua()` — nueva función en `classify.py`

**Por qué:** `set_code` + categoría + idioma coincidentes no bastan si la cantidad/unidad de venta es distinta — un Case de 10-12 cajas, un pack de 5 sobres, o una caja descrita con "x12" en vez de las 24 estándar, no son el mismo SKU que una caja o un sobre suelto, aunque compartan set_code.

**Modelo, verificado contra 6 casos reales del CSV (todos OK):**

```python
## Cantidad estándar de contenido para categorías que SON, por naturaleza,
## un contenedor de varias unidades (una caja SIEMPRE trae N sobres, eso no
## es un bundle, es describir el producto). Solo se marca ambiguo cuando el
## número mencionado DIFIERE del estándar conocido de esa categoría.
_CANTIDAD_ESTANDAR_POR_CATEGORIA = {
    "booster-box": 24,
    "premium-collection": 20,  # verificado contra el catálogo real: "Caja de 20 Sobres PRB02"
}

## Categorías que son, por naturaleza, UNA sola unidad de venta -- cualquier
## cantidad >1 mencionada en el nombre es sospechosa de ser un bundle real
## (Pack 5 Sobres, x6 mazos...), no una descripción de contenido normal.
_CATEGORIAS_UNIDAD_UNICA = {
    "booster-pack", "starter-deck", "illustration-box", "playmat",
    "devil-fruits-collection", "double-pack", "learn-deck",
    "promo-card", "mystery-pack", "dice-accessory",
}

_CANTIDAD_SOSPECHOSA_RE = re.compile(r"\bpack\s*(\d+)\b|\b(\d+)\s*sobres\b|\bx\s*(\d+)\b", re.IGNORECASE)


def cantidad_es_ambigua(raw_name: str, category_slug: str) -> bool:
    """True si raw_name menciona una cantidad que sugiere una unidad de
    venta distinta a la del canónico -- ver _CANTIDAD_ESTANDAR_POR_CATEGORIA
    y _CATEGORIAS_UNIDAD_UNICA arriba. Categoría no reconocida en ninguna
    de las dos listas -> no se arriesga un falso positivo por exceso de
    celo, se devuelve False."""
    match = _CANTIDAD_SOSPECHOSA_RE.search(raw_name)
    if not match:
        return False
    numero = int(next(g for g in match.groups() if g))
    if category_slug in _CANTIDAD_ESTANDAR_POR_CATEGORIA:
        return numero != _CANTIDAD_ESTANDAR_POR_CATEGORIA[category_slug]
    if category_slug in _CATEGORIAS_UNIDAD_UNICA:
        return numero != 1
    return False
```

#### 1.3 Categoría nueva `booster-case`, y `classify_product()` reconoce "Case" como tipo propio

**Por qué:** en vez de que "Case" se quede para siempre en `needs_review` (guarda barata pero sin salida), se sigue la decisión ya tomada de darle categoría propia — así un Case sí puede llegar a `confirmed` contra su propio canónico, sin arriesgar confundirlo con una Booster Box.

```python
## CLASSIFICATION_RULES -- antes de BOOSTER_BOX (si no, "Case ... Booster
## Box" matchearía como BOOSTER_BOX primero por orden de lista)
("BOOSTER_CASE", ["booster case", "case -", "(case)", "booster box case"]),
```

`PRODUCT_TYPE_TO_CATEGORY_SLUG["BOOSTER_CASE"] = "booster-case"`. La categoría se siembra en `seed-catalog-app-tcg.sql` como hija de `Sellado`, igual que las demás.

**Pendiente de decidir en la propia siembra, no aquí:** `seed_official_catalog.py` necesita generar los canónicos de Case (probablemente derivados de cada Booster Box existente, con el multiplicador de cajas por case que corresponda a cada línea de producto — no todos son 12, `PRB02` era `x10` en el CSV real) — esto es trabajo de siembra, no de `matcher.py`, y se deja fuera del alcance de este documento.

#### 1.4 Reestructuración de `promo-card` bajo `single-card`

**Por qué:** una carta promocional individual no es "producto sellado" en el mismo sentido que una caja/sobre — encaja mejor como categoría propia (`single-card`, junto a `Sellado`/`Accesorios` como tercer padre) con `promo-card` como hija, en vez de mezclada bajo `Sellado`.

**Cambio:** `seed-catalog-app-tcg.sql` — nuevo padre `single-card`, mover `promo-card` de hijo de `Sellado` a hijo de `single-card`. No afecta a `classify.py`/`matcher.py` (la categoría sigue siendo `promo-card`, solo cambia su padre en la jerarquía).

#### 1.5 `_evaluate()` — la lógica completa con las cinco condiciones

```python
candidate, es_fallback = _best_candidate(cur, category_id, raw_name, classification.set_code)
if not candidate:
    return MatchOutcome("unmatched", None, None)

product_id, set_code, language, score = candidate
set_code_matches = classification.set_code is not None and set_code == classification.set_code
set_code_conflicts = classification.set_code is not None and set_code is not None and set_code != classification.set_code

if set_code_conflicts:
    return MatchOutcome("unmatched", None, None)

if not set_code_matches and score < REVIEW_SIMILARITY_THRESHOLD:
    return MatchOutcome("unmatched", None, None)

language_matches = classification.language is not None and language == classification.language
cantidad_ok = not cantidad_es_ambigua(raw_name, category_slug)

## Las CINCO condiciones a la vez: categoría real (ya garantizado si llegamos
## aquí), candidato primario (no fallback), set_code exacto, idioma exacto,
## cantidad no ambigua.
if set_code_matches and not es_fallback and language_matches and cantidad_ok:
    return MatchOutcome("confirmed", product_id, score)

if language_matches and score > CONFIRMED_SIMILARITY_THRESHOLD:
    return MatchOutcome("confirmed", product_id, score)

return MatchOutcome("needs_review", None, None)
```

---

### 2. Set de pruebas — centrado en falsos positivos, con casos reales del CSV

Cada caso de la tabla de "NO debe confirmar" es un producto real de `multi_tienda_one_piece.csv` que comparte `set_code` con un candidato real del catálogo — exactamente el escenario que dispararía un falso positivo si alguna de las cinco condiciones no se comprobara bien.

#### 2.1 Control positivo — deben confirmar (ya revisados a mano, sección anterior)

```python
@pytest.mark.parametrize("raw_name,raw_variant,category_slug,candidato_set_code,candidato_idioma", [
    ("One Piece: Double Pack Set Display DP-11", None, "double-pack", "DP11", "EN"),
    ("One Piece Card Game Playmat Limited Edition Vol 2", None, "playmat", "VOL02", "EN"),
    ("One Piece | Illustration Box Vol.4 Perona & Mihawk", "Inglés", "illustration-box", "VOL04", "EN"),
    ("One Piece Card Game - Devil Fruits Collection Vol.3 Op-Op Fruit (DF03)", None, "devil-fruits-collection", "DF03", "EN"),
    ("Caja de 20 Sobres The Best 2 PRB02 - Inglés", None, "premium-collection", "PRB02", "EN"),
    ("One Piece Card Game - Gear 5 Starter Deck EX ST21", None, "starter-deck", "ST21", "EN"),
    ("Caja sobres One Piece OP-16 The Time of Battle (inglés)", None, "booster-box", "OP16", "EN"),
    ("ONE PIECE TCG - EB-05", None, "booster-pack", "EB05", "EN"),
])
def test_setcode_exacto_confirma_casos_reales_verificados_a_mano(raw_name, raw_variant, category_slug, candidato_set_code, candidato_idioma, db_conn):
    """Los 8 primeros de la muestra de 20 revisada a mano (2026-08-27) --
    todos confirmados como el producto correcto. Regresión: si alguno deja
    de confirmar, algo del cambio rompió el camino feliz."""
    ...
    assert resultado.match_status == "confirmed"
```

#### 2.2 Falsos positivos a evitar — el núcleo de este set de pruebas

```python
@pytest.mark.parametrize("raw_name,raw_variant,motivo_del_riesgo", [
    (
        "Carta Promo Sellada Ichiban Kuji Monkey D. Luffy OP13 - Japones", None,
        "cross_categoria: promo-card está vacía, el candidato solo aparece "
        "por el fallback de set_code en todo el catálogo (Booster Pack "
        "OP-13, score=0.084) -- NO es el mismo producto",
    ),
    (
        "(CASE) THE BEST 2 – PRB-02 – x10 Booster Box- One Piece Card Game", None,
        "cantidad_ambigua: es un Case (10 cajas), no una caja suelta -- "
        "debe clasificarse BOOSTER_CASE, no confirmar contra Premium "
        "Booster Box",
    ),
    (
        "Pack 5 Sobres One Piece Adventure on KAMI’s Island OP15 - Japones", None,
        "cantidad_ambigua: bundle real de 5 sobres, booster-pack es "
        "categoría de unidad única -- 5 != 1",
    ),
    (
        "[INGLÉS] One Piece Card Game OP-16 Caja de sobres x12", None,
        "cantidad_ambigua: booster-box espera 24, x12 no coincide -- "
        "podría ser una caja distinta, no confiar solo en el set_code",
    ),
    (
        "[INGLÉS] One Piece Card Game Starter Deck EX Gear5 [ST21] x6", None,
        "cantidad_ambigua: starter-deck es categoría de unidad única -- "
        "6 != 1",
    ),
    (
        "One Piece | Sobres OP-03 Pillars of Strength", "Japonés",
        "idioma_no_coincide: raw es JP, el único candidato con ese "
        "set_code en booster-pack es EN -- no existe canónico JP para "
        "este caso concreto todavía",
    ),
    (
        "Caja One Piece The Best 2 PRB02 - Japones", None,
        "idioma_no_coincide: mismo caso, premium-collection no tiene "
        "variante JP sembrada (a diferencia de booster-box/pack)",
    ),
    (
        "One Piece Card Game OP-18 Booster Pack - English", None,
        "setcode_distinto: OP-18 no existe en el catálogo sembrado "
        "(lanzamiento posterior) -- NUNCA debe confirmar contra OP-13 u "
        "otro set solo porque el texto se parezca",
    ),
])
def test_setcode_exacto_NO_confirma_falsos_positivos_reales(raw_name, raw_variant, motivo_del_riesgo, db_conn):
    """Casos reales de multi_tienda_one_piece.csv que comparten set_code
    (o casi) con un candidato real y que, sin las guardas de este cambio,
    confirmarían incorrectamente. Cada uno reproduce exactamente un
    hallazgo de la investigación de matching (2026-08-27) -- ver
    el resto de esta sección."""
    resultado = evaluar(raw_name, raw_variant)
    assert resultado.match_status != "confirmed", f"Falso positivo: {motivo_del_riesgo}"
```

#### 2.3 `cantidad_es_ambigua()` — unitarios directos, sin necesitar BBDD

```python
@pytest.mark.parametrize("raw_name,category_slug,esperado", [
    ("Caja de 24 Sobres Royal Blood OP10 - Inglés", "booster-box", False),
    ("Caja de 20 Sobres The Best 2 PRB02 - Inglés", "premium-collection", False),
    ("[INGLÉS] One Piece Card Game OP-16 Caja de sobres x12", "booster-box", True),
    ("Pack 5 Sobres One Piece Adventure on KAMI’s Island OP15 - Japones", "booster-pack", True),
    ("[INGLÉS] One Piece Card Game Starter Deck EX Gear5 [ST21] x6", "starter-deck", True),
    (
        "One Piece Card Game Double Pack Set Vol.10 [DP-10] – 2 Booster Packs + Exclusive DON!! Card",
        "double-pack", False,
    ),  # "2 Booster Packs" describe el contenido normal de un Double Pack, no un bundle de 2 sets
])
def test_cantidad_es_ambigua_casos_reales(raw_name, category_slug, esperado):
    """Verificado contra el CSV real antes de escribirse."""
    assert cantidad_es_ambigua(raw_name, category_slug) == esperado
```

#### 2.4 `_best_candidate()` — distinción primario/fallback

```python
def test_candidato_por_fallback_cross_categoria_se_marca_como_tal(db_conn):
    """promo-card vacía a propósito en este test -- el único candidato
    posible para 'OP13' viene de fuera de la categoría derivada."""
    candidate, es_fallback = _best_candidate(cur, category_id_promo_card, "Carta Promo... OP13...", "OP13")
    assert es_fallback is True

def test_candidato_dentro_de_su_categoria_no_se_marca_como_fallback(db_conn):
    candidate, es_fallback = _best_candidate(cur, category_id_booster_box, "Caja OP16...", "OP16")
    assert es_fallback is False
```

---

### 3. Resumen de lo que este set de pruebas protege

| Sin esta prueba... | ...este bug pasaría desapercibido |
|---|---|
| 2.2, caso 1 | Una carta suelta se confirmaría como si fuera un producto sellado completo |
| 2.2, casos 2-5 | Un Case/bundle se confirmaría al precio de una unidad suelta — error de precio visible |
| 2.2, casos 6-7 | Un producto JP se confirmaría contra el canónico EN equivocado |
| 2.2, caso 8 | Un lanzamiento futuro se confirmaría contra un set antiguo por similitud de texto engañosa |
| 2.3 | La función de cantidad ambigua podría marcar como sospechoso algo normal (regresión ya vista una vez con mi propia regex durante la investigación) |
| 2.4 | El fallback cross-categoría podría heredar por accidente el mismo nivel de confianza que un match real |

Ningún test de esta lista es hipotético — los ocho de la sección 2.2 son productos reales de tu CSV, encontrados uno a uno durante la investigación, no inventados para la ocasión.


---

# Parte 2 — Propuesta de mejoras (sesión de revisión sobre datos reales)

## Propuesta de mejoras — Motor de matching (sesión de revisión sobre datos reales)

Consolida los hallazgos de revisar la cola de 187 `needs_review` real contra el código ya desplegado (`161bae5`, `4f5d83a`, `9b75e37`). Incluye una acción inmediata sin código, dos cambios de código concretos, y una decisión que se deja explícitamente cerrada para no reabrirla sin evidencia nueva.

**Hecho (2026-08-28):** los 4 puntos implementados, más un hallazgo real no cubierto por el documento original. `needs_review` 182→158 (-13%), `confirmed` +20 en la pasada real. Detalle en cada punto más abajo.

---

### 0. Acción inmediata, sin escribir código — hacedla antes que nada de lo de abajo

**Volver a ejecutar `matcher.run_matching()` contra la base de datos real de producción.**

Verificado con el código actual: `DP-08`, `DP-07`, `DP-09`, `Illustration Box Vol.5`, `Vol.6`, `DON!! Card Pack DP-06` y `DP-05` (EN+JP) **ya confirman correctamente hoy** — la cola que se revisó sigue mostrando decisiones tomadas con el motor de antes de estos tres commits. Antes de escribir ni una línea más, volver a correr el matching real debería mover una parte importante de las 187 filas a `confirmed` sin ningún cambio adicional.

```python
from persistence import get_connection
from matcher import run_matching
conn = get_connection()
print(run_matching(conn))
conn.close()
```

**Corregido (2026-08-28):** los ejemplos citados (`DP-08`, `DP-07`, `DON!! Card Pack DP-06`/`DP-05`) **NO confirmaban solo con re-ejecutar** -- verificado contra la BBDD real que la premisa de este punto estaba probada sin `raw_tags`, y el pipeline real sí los usa. Causa real encontrada: `raw_tags` genéricas de catálogo (ej. `"Caja One Piece, Cajas, ..."`) hacían que `product_type` saliera `BOOSTER_BOX` en vez de `DOUBLE_PACK`, pese a que el propio `DP-07` está en el nombre -- mismo problema de fondo que `BOOSTER_CASE`+tags (commit `9b75e37`). Arreglado generalizando el mecanismo del punto 1 (ver ahí) a `DOUBLE_PACK` también.

---

### 1. Detectar `DF-NN` por patrón de código, no por palabra clave de idioma

**Problema:** `"Fruta del Diablo vol.1 [DF-01]"` extrae `set_code=DF01` correctamente, pero `product_type` cae en `OTROS` porque `DEVIL_FRUITS_COLLECTION` solo reconoce la palabra clave en inglés (`"devil fruits collection"`). Afecta a las 3 filas `DF-01`/`DF-02`/`DF-03` vistas en la cola real.

**Por qué esta solución y no "añadir la palabra en español":** verificado contra las 194 líneas del catálogo oficial completo — **ningún otro producto usa el prefijo `DF`**. Es una señal tan fiable como el propio `set_code`, y a diferencia de una palabra clave, no depende de en qué idioma esté escrito el resto del texto — cubre español, catalán, o directamente ninguna palabra descriptiva, sin tener que volver a tocarlo nunca.

```python
## classify.py
_DF_CODE_RE = re.compile(r"\bdf[\s-]?0?\d{1,2}\b", re.IGNORECASE)

def classify_product(name: str, variant_title: Optional[str] = None) -> Classification:
    combined = name + " " + (variant_title or "")
    if _DF_CODE_RE.search(combined):
        set_code = _extract_set_code(combined)
        language = _detect_language(combined, variant_title)
        return Classification(product_type="DEVIL_FRUITS_COLLECTION", set_code=set_code,
                               main_set=None, language=language)
    # resto de CLASSIFICATION_RULES sin cambios
    ...
```

**Tests, con los tres casos reales que motivaron el hallazgo:**

```python
@pytest.mark.parametrize("raw_name,esperado_set_code", [
    ("One Piece | Fruta del Diablo vol.1 [DF-01] Inglés 2023", "DF01"),
    ("One Piece | Fruta del Diablo Mera Mera vol. 2 [DF-02]", "DF02"),
    ("One Piece | DF03 Fruta del Diablo Ope Ope No Mi vol. 3 OP12", "DF03"),
])
def test_df_code_clasifica_devil_fruits_sin_depender_del_idioma(raw_name, esperado_set_code):
    """Casos reales de la cola de revisión (2026-08-28) -- 'Fruta del
    Diablo' no estaba reconocido como keyword de DEVIL_FRUITS_COLLECTION,
    pese a que el código DF-NN ya identifica el producto sin ambigüedad
    (verificado: DF es prefijo exclusivo en todo el catálogo oficial)."""
    c = classify_product(raw_name)
    assert c.product_type == "DEVIL_FRUITS_COLLECTION"
    assert c.set_code == esperado_set_code
```

**Hecho (2026-08-28):** implementado, generalizado a `DOUBLE_PACK` (ver punto 0) porque el mismo problema de tags le afectaba igual. No como override incondicional (a diferencia de `BOOSTER_CASE`) -- solo se aplica cuando `name+variant` a secas (sin tags) no dan NINGÚN tipo (`_match_keyword_type` factorizado para poder comprobarlo por separado); si el propio nombre ya trae una keyword real de otro tipo, esa señal sigue ganando al código. El snippet de arriba era ilustrativo, no literal -- la implementación real vive en `classify_product()` integrada con `CLASSIFICATION_RULES`/`_DON_CARD_RE`/LOTE_CARTAS, ver `shared/classify.py`. Tests en `test_classify_product.py::TestDFCodePorPatronDeCodigo` (los 3 de arriba + keyword real gana al código) y `TestExtraTypeHintDeTags::test_codigo_dp_gana_a_tag_generico_de_caja`.

---

### 2. Rangos de código (`"ST-15 - ST-20"`, `"[ST-31]~[ST-36]"`) extraen un código equivocado

**Problema, encontrado al revisar la cola completa:** `"One Piece Sobre ST-15 - ST-20 Release Event Pack"` extrae `set_code=ST15` — el primer extremo del rango, como si fuera el código propio del producto. Como la categoría es `booster-pack` (por "Sobre") y ningún sobre lleva prefijo `ST`, esto dispara el fallback cross-categoría y devuelve un Starter Deck real pero completamente ajeno como candidato. Afecta a **6 filas**: `"ST-15 - ST-20 Release Event Pack"` (EN+JP) y `"[ST-31]~[ST-36] Participation/Winner pack"` (2 nombres × 2 idiomas).

**Por qué es un rango y no un código propio:** estos productos son packs promocionales ligados a **todo un lote de mazos** (de ST-15 a ST-20, de ST-31 a ST-36), no a uno solo — el mismo fenómeno que "Tournament Pack"/"Event Pack" (sin código real), solo que aquí la extracción coge un extremo del rango pensando que es un código válido.

```python
## classify.py -- detectar el patrón de rango ANTES de extraer un código suelto
_RANGO_CODIGOS_RE = re.compile(r"\b[A-Z]{2,3}-?\d{1,3}\s*[-~]\s*(?:\[)?[A-Z]{0,3}-?\d{1,3}(?:\])?", re.IGNORECASE)

def _extract_set_code(text: str) -> Optional[str]:
    if _RANGO_CODIGOS_RE.search(text):
        return None  # rango -- no pertenece a un único producto, no es un código propio
    # extracción normal existente, sin cambios
    ...
```

**Tests, con los casos reales:**

```python
@pytest.mark.parametrize("raw_name", [
    "One Piece Sobre ST-15 - ST-20 Release Event Pack",
    "One Piece Sobre Beginners Deck Party ST-31 - ST-36 Participation Pack",
    "One Piece Sobre Beginners Deck Party [ST-31]~[ST-36] Winner pack",
])
def test_rango_de_codigos_no_extrae_el_primer_extremo(raw_name):
    """Casos reales de la cola de revisión (2026-08-28): un rango de
    códigos (producto ligado a varios mazos, no a uno) extraía el primer
    extremo como si fuera el set_code propio, disparando un fallback
    cross-categoría contra un Starter Deck ajeno."""
    c = classify_product(raw_name)
    assert c.set_code is None
```

**Hecho (2026-08-28):** implementado, pero con una corrección real sobre la regex propuesta arriba -- probada contra la suite completa, colisionaba con la numeración de CARTA INDIVIDUAL ("Rebecca (OP10-058)", "EB02-003"), tratando `-058`/`-003` como si fueran el segundo extremo de un rango y perdiendo el `set_code` real de esos casos ya cubiertos en otro sitio. Corregida para exigir el mismo prefijo real (`_SET_CODE_PREFIXES`) en AMBOS extremos, no `[A-Z]{2,3}`/`{0,3}` genérico -- ver `_RANGO_CODIGOS_RE` en `shared/classify.py`. Tests con los 3 casos reales + los 2 de regresión de carta individual en `TestRangoDeCodigos`.

---

### 3. Decisión: NO bajar el umbral de confirmación para categorías sin código (`premium-collection`)

**Motivo de la pregunta:** `"Premium Card Collection Best Selection - Ace, Sabo and Luffy"` encuentra el candidato correcto con score `0.563` — justo por debajo del `0.6` necesario, sin ningún `set_code` al que agarrarse (las colecciones premium no llevan código `OP-NN`/`ST-NN`).

**Decisión, con evidencia delante:** no bajar el umbral para este caso. Verificado que `premium-collection` tiene **22 colecciones distintas** (44 con EN/JP) con nombres muy parecidos entre sí — `"Best Selection"`, `"Best Selection Vol.2"` hasta `"Vol.7"`, `"Leader Collection"`, `"25th Edition"`, `"29th Anniversary Edition"`... En la propia búsqueda de este caso, `"Best Selection"` (0.48) y `"Best Selection Vol.2"` (0.44) aparecen como candidatos alternativos — bajar el umbral lo suficiente para confirmar automáticamente arriesgaría confirmar la colección **equivocada** entre dos casi idénticas, un error peor que dejarlo en revisión manual.

**Vía de mejora real, no immediata:** el catálogo original (`data/one_piece_tcg_products.json`) sí tiene el MSRP oficial de cada colección (`msrp_usd`), nunca cargado a la tabla `product`. Podría servir de señal de apoyo para desambiguar colecciones parecidas — pero necesita validarse antes de confiar en ello (el precio de una tienda no es el MSRP oficial, habría que comprobar si la comparación *relativa* entre colecciones se mantiene fiable). Se deja anotado como investigación futura, no como tarea de este documento.

---

### 4. Recordatorio — sigue pendiente de tu decisión, con más evidencia real esta sesión

`starter-deck` sigue fuera de la lista de categorías con variante JP (`_JP_VARIANT_CATEGORY_SLUGS` solo cubre `booster-box`/`booster-pack`/`booster-case`/`double-pack`/`premium-collection`). Esta sesión aportó **7 ejemplos reales más** de demanda JP en starter deck (`ST-14`, `ST-11`, `ST-09`, `ST-08`, `ST-36`, `ST-34`, `ST-33`, todos de Pokemillon) — la señal de volumen que se dejó como condición para ampliar la lista ya está aquí.

**Hecho (2026-08-28):** confirmado. `starter-deck` añadida a `_JP_VARIANT_CATEGORY_SLUGS` -- 40 canónicos JP nuevos sembrados (`seed_official_catalog.py`).

---

### Resumen de cambios de este documento

| # | Cambio | Tipo | Estado |
|---|---|---|---|
| 0 | Re-ejecutar `run_matching()` en producción | Acción, sin código | Hecho -- premisa corregida (raw_tags), no bastaba solo con re-ejecutar |
| 1 | `DF-NN` clasifica por patrón de código, no por palabra clave | Código + tests | Hecho, generalizado a `DOUBLE_PACK` |
| 2 | Rangos de código (`"NN - NN"`, `"[NN]~[NN]"`) extraen `None` | Código + tests | Hecho -- regex corregida (colisión con carta individual) |
| 3 | No bajar el umbral para categorías sin código | Decisión cerrada, no una tarea | Confirmada, sin cambios |
| 4 | `starter-deck` a la lista de variante JP | Pendiente de tu confirmación | Hecho -- confirmado |

**Resultado real (pasada completa contra producción, 2026-08-28):** `needs_review` 182→158 (-13%), `confirmed` 1069→1089 (+20), `not_applicable` 568→566. 406 tests en `store_monitor` (+11), 201 en `api` (sin cambios este documento).


---

# Parte 3 — Pendientes (consolidado, incluye la auditoría 2026-08-29)

## Pendientes — Motor de matching (consolidado)

Solo lo que queda por hacer o decidir. El razonamiento completo de cada punto vive en la sección "Implementación — auto-confirmado por `set_code` exacto" de este mismo documento — aquí solo la lista accionable.

---

### Bugs a corregir

#### 1. `is_prerelease_variant()` — sin implementar todavía
Falso positivo real confirmado en producción de prueba: `"Mazo Iniciación Los Mugiwaras ST-01"` (sin mención de pre-release) confirmó contra `"(Pre-release) ST-01 PRE"` con score `0.08`. Especificación completa, con test de regresión y su control positivo, ya escrita en `docs/matching/motor-matching.md` sección 4 — falta implementarla.

#### 2. `BOOSTER_CASE` — la palabra clave nueva es demasiado amplia, colisiona con "Card Case"
Encontrado al verificar el commit `161bae5`: la categoría `booster-case` tiene sembrado un único producto, y es incorrecto — `"Limited Card Case -Monkey.D.Luffy- EN"` (un estuche/funda de cartas, producto ya existente y correcto en sí mismo) coló como `BOOSTER_CASE` porque el patrón `"case -"` es demasiado genérico. Efecto práctico: `booster-case` está, a día de hoy, **vacía de contenido real** — cualquier "Case de sobres" real que llegue de una tienda caerá en el mismo problema de categoría vacía que se intentaba resolver (fallback cross-categoría). Arreglo: acotar el patrón (`"booster case"`, `"box case"`, `"(case)"` seguido de contexto de sobres/cajas, no `"case -"` a secas) y quitar el producto mal clasificado.

**Hecho (2026-08-28):** `BOOSTER_CASE` salió de `CLASSIFICATION_RULES` (un keyword suelto no bastaba) y pasó a `_BOOSTER_CASE_RE`/`_BOOSTER_CASE_CONTEXT_RE` en `shared/classify.py` — "case" a secas + contexto (código de set reconocible o palabra de caja/booster/sellado) en el mismo texto. Validado fila a fila contra las 34 menciones reales de "case" en `multi_tienda_one_piece.csv`: cubre tanto los Case con "booster"/"box" pegado como los que no lo llevan (`"OP-19 Case"`, `"Case sellado OP-16"`, `"Case OP02 Paramount War (12 cajas)"`), y excluye los 7 accesorios reales (`Dice Case`/`Card Case`/`Playmat...Case`, más el Funko Pop) sin necesitar mirar en qué categoría cayeron. Tests de regresión en `test_classify_product.py`.

**Pendiente, fuera del alcance de un cambio de código:** el producto `"Limited Card Case -Monkey.D.Luffy- EN"` ya sembrado con la categoría vieja (equivocada) en tu BBDD real sigue ahí — `seed_official_catalog.py` es idempotente por `name_canonical` (`_insert_if_new`), así que re-ejecutarlo NO corrige una fila ya existente, solo evita crear nuevas mal clasificadas. Hace falta corregirlo a mano:

```sql
DELETE FROM product WHERE name_canonical = 'Limited Card Case -Monkey.D.Luffy- EN';
```

(o reasignar su `category_id` en vez de borrarlo, si prefieres conservarlo bajo otra categoría de accesorios) y volver a ejecutar `seed_official_catalog.py` para que se regenere si corresponde.

#### 3. `booster-case` necesita productos canónicos reales sembrados
Una vez arreglado el punto 2, la categoría sigue sin ningún Case real. Pendiente: generar los canónicos (probablemente derivados de cada Booster Box existente) — **el multiplicador de cajas por Case no es uniforme**, verificado en el propio CSV: `PRB-02` es `x10`, los `OP-NN` vistos son `x12`. No asumir un único número para todas las líneas de producto.

**Hecho (2026-08-28):** `seed_official_catalog.py` genera ahora un Case (EN+JP) por cada release booster con multiplicador conocido — `_CASE_MULTIPLIER_BY_CATEGORY = {"booster-box": 12, "premium-collection": 10}`, derivado de `box_category_slug` (la categoría real que ya resuelve `classify_product()` para la variante caja), no del prefijo del código. `starter-deck`/`double-pack` se dejan fuera A PROPÓSITO — cero menciones reales de "Case" para esas líneas en las 34 filas revisadas, no se inventa un multiplicador sin dato. Verificado con una siembra real completa contra `cartitas_test`: 46 productos `booster-case` (23 releases × EN/JP), todos con `set_code`/`language` correctos y ninguno colisionando con `"Limited Card Case -Monkey.D.Luffy-"` (confirmado ausente de los sembrados, correctamente omitido como `OTROS`).

#### 4. `"Don!! (DP10 Map) - One Piece Products (DON!!)"` — falso positivo confirmado, no solo duda
Tu valoración: tiene pinta de ser una **carta suelta promocional DON!!** (la que viene de regalo dentro del Double Pack Set Vol.10, tematizada como mapa), no el Double Pack sellado completo — vendida por separado como coleccionable individual. Si es así, hoy confirma incorrectamente contra `"Double Pack Set Vol.10 DP-10 EN"`, un producto sellado con una unidad de venta y un precio completamente distintos.

Mismo patrón de fondo que la carta promo `Ichiban Kuji`/`OP13` del arranque de esta investigación (una carta individual coincide de código con un producto sellado que la contiene o la acompaña) — pero ahí el problema era el *fallback cross-categoría* sobre una categoría vacía (`promo-card`); aquí es distinto: `classify_product()` clasificó esto directamente como `DOUBLE_PACK` (por detectar "double pack"/`DP10` en el texto), cuando en realidad es una carta suelta, no el pack sellado.

Pendiente: revisar si `classify_product()` necesita una señal adicional para "esto es un insert/carta suelta de regalo, no el producto sellado que la acompaña" — patrones como "Don!! Card" mencionado junto a un código de set, sin palabras como "set"/"pack"/"caja" que confirmen que es el sellado completo. Similar en espíritu a la lista `LOTE_CARTAS` (`cgc`/`psa`/`bgs`) pero para un caso distinto (no cartas gradeadas, cartas promocionales de regalo).

**Hecho (2026-08-28):** guard nuevo en el fallback de código `DP-NN` (`_DON_CARD_RE`/`_SEALED_PRODUCT_CONTEXT_RE` en `shared/classify.py`) — "Don!!" + código, SIN ninguna de "set"/"pack"/"caja"/"box"/"sobre" en el mismo texto, clasifica `PROMO_CARD` en vez de `DOUBLE_PACK`. Se eligió `PROMO_CARD` (no `LOTE_CARTAS`) porque sí tiene sentido comparable contra un canónico si algún día se siembra uno (punto 8 sigue abierto). Validado contra las 4 menciones reales de "Don!!"+código DP en el CSV: solo la fila del bug cambia — las otras 3 (`"Special DON!! Card Pack DP-06..."`, `"...DP-05..."`, el propio `"Double Pack Set Vol.10 [DP-10]..."`) siguen `DOUBLE_PACK` sin tocar, porque sí traen "Pack"/"Set" en el texto.

---

### Casos a verificar manualmente (no son bugs de código, necesitan tu criterio)

#### 5. ~~`"One Piece | DP-08 Legacy of the Master OP-12"` → confirmado contra `"Double Pack Set Vol.8 DP-08 EN"`~~ — verificado, correcto

Confirmado por ti: `DP-08` se lanzó junto a "Legacy of the Master" (OP-12) — mi dato del catálogo oficial estaba desactualizado/equivocado en esa asociación, no la tienda. El match es correcto, sin ninguna acción pendiente.

---

### A tener en cuenta — no bloqueante, sin decisión tomada todavía

#### 6. Ampliar la siembra JP a todas las categorías de producto sellado con demanda real — decidido

Decisión: no limitar `_JP_VARIANT_CATEGORY_SLUGS` a `booster-box`/`booster-pack` — ampliar a **todo lo que sea producto sellado con importación japonesa real**: `booster-pack`, `booster-box`, `booster-case` (una vez sembrado, punto 3), `double-pack`, `premium-collection`. Motivado por la demanda ya demostrada en el CSV (16 filas `PRB-01`/`PRB-02` en japonés que hoy no pueden confirmar por falta de esa variante).

**Decidido (2026-08-29):** `starter-deck` entra también en la ampliación — señal de demanda real confirmada en la auditoría de `needs_review` (Pokemillon vendía 7+ Starter Decks japoneses distintos, ST-08/ST-09/ST-11/ST-14/ST-33/ST-34/ST-36, sin ningún candidato JP posible). `illustration-box`/`devil-fruits-collection`/`learn-deck` siguen fuera de la lista explícita, a falta de confirmación, no por descarte. Playmat/dice-accessory/promo-card/mystery-pack se asumen fuera por naturaleza (accesorios o promocionales, no producto sellado que se importe en volumen).

Además, quedan **29 filas residuales** dentro de `booster-box`/`booster-pack` con idioma no coincidente **pese a que esas categorías ya tienen JP sembrado** — causa todavía sin investigar, podría no ser un problema de dato faltante sino un fallo puntual del scoring al elegir entre EN/JP cuando ambas variantes ya existen (ver duplicados de `set_code` en `docs/matching/motor-matching.md`).

**Hecho (2026-08-28), la parte decidida:** `_JP_VARIANT_CATEGORY_SLUGS` en `seed_official_catalog.py` ampliada a `{"booster-box", "booster-pack", "booster-case", "double-pack", "premium-collection"}`. `starter-deck`/`illustration-box`/`devil-fruits-collection`/`learn-deck` siguen fuera, tal como queda "sin decidir" arriba -- no se tocaron. Las 29 filas residuales y la investigación del fallo de scoring EN/JP siguen sin investigar, fuera de esta ronda.

**Hecho (2026-08-28/29, confirmado en dos sesiones de auditoría independientes con la misma evidencia -- ver también la sección "Propuesta de mejoras" de este documento, punto 4):** `starter-deck` añadida a `_JP_VARIANT_CATEGORY_SLUGS` -- 7 ejemplos reales de demanda JP (Pokemillon) confirmaron la señal de volumen que faltaba, 40 canónicos JP nuevos sembrados sobre `cartitas` real (uno por cada Starter Deck EN existente). `illustration-box`/`devil-fruits-collection`/`learn-deck` siguen sin decidir, sin evidencia todavía.

#### 7. Ambigüedad de texto genuina (233→215 filas del análisis, ~14% del total no confirmado) — investigado, no es un problema de categorías

**Conclusión del estudio: el 98.6% (212 de 215) SÍ tiene categoría correcta asignada.** No falta ninguna categoría — el motivo real es que estas filas no tienen ningún `set_code` que extraer, porque **son productos promocionales/de evento que quedan fuera de la numeración regular de Bandai** (`OP-NN`, `ST-NN`...), no un fallo de clasificación.

**Relación entre ellas — sí, y es clara:** se agrupan en un puñado de familias que se repiten:
- `"Tournament Pack 2024 Oct.-Dec."`, `"Tournament pack 2026 Vol. 2"` — packs de torneo periódicos
- `"Event Pack Vol. 4"`, `"Welcome Pack Vol. 1"` — packs de evento/bienvenida
- `"3rd Anniversary! ... Campaign Pack"`, `"2nd Anniversary Tournament Pack"` — packs de aniversario
- `"Special Pack VJUMP Tokudaigou"` — promo de revista japonesa

Es el mismo fenómeno de fondo que las categorías vacías del punto 8 (`promo-card`/`mystery-pack`) — distribución promocional de Bandai, no catálogo de venta regular — solo que estos casos concretos sí llevan la palabra "Sobre"/"Pack" en el nombre y por eso aterrizan en `booster-pack` en vez de quedarse sin categoría. **No hay atajo estructural limpio para esto**: no tienen código que corroborar porque genuinamente no lo tienen. Quedan en `needs_review`/`unmatched` correctamente a falta de sembrar sus propios canónicos (si se decide que vale la pena, mismo criterio que el punto 8).

**Dos hallazgos concretos y distintos, encontrados al revisar el resto (3 de 215):**

- **`"Caja One Piece Devil Fruits Collection Vol.2 - Ingles"` no extrae `set_code`.** Debería reconocer `"Vol.2"` → `DF02`, igual que ya hace `Illustration Box` con su convención `Vol.N → IB-0N`. Gap de extracción real, arreglo acotado.
- **`"One Piece | Caja Aprende a Jugar"` clasifica como `BOOSTER_BOX`** (por la palabra "Caja") **en vez de `LEARN_DECK`** — probablemente porque la regla de `LEARN_DECK` no tiene la frase en español ("aprende a jugar") en su lista de palabras clave, y `BOOSTER_BOX` la captura primero por orden de lista.

**Hecho (2026-08-28), los dos hallazgos de arriba:** `DEVIL_FRUITS_COLLECTION` ahora prueba el código explícito primero (`"(DF03)"`, sin cambios) y cae a `Vol.N -> DF0N` cuando no hay ninguno -- verificado que ambos números coinciden en el propio catálogo (`"Vol.3 Op-Op Fruit (DF03)"`). `"aprende a jugar"` añadida a los keywords de `LEARN_DECK` (ya iba antes que `BOOSTER_BOX` en el orden de la lista, solo le faltaba el keyword).

**Nota aparte, no parte de este bucket:** 3 filas de `OP-18`/`OP-19` en `booster-case` no tienen candidato — correcto, son lanzamientos futuros sin sembrar todavía (mismo caso ya conocido, no una ambigüedad nueva).

#### 8. `mystery-pack` nunca tendrá producto canónico (decidido) — `promo-card` queda abierto

**`mystery-pack` — decisión permanente, no pendiente:** por su propia naturaleza (contenido aleatorio/sorpresa), no existe un "producto canónico" contra el que comparar — no tiene sentido sembrar nada aquí nunca. Se queda vacía a propósito, para siempre.

**`promo-card` — de momento no se dan de alta las cartas individuales, pero puede cambiar.** No es una decisión cerrada como `mystery-pack`: sigue abierta la puerta a sembrar canónicos de cartas promo concretas en el futuro (por ejemplo, a partir de lo que aparezca con volumen real en `missing-candidates`), solo que no se hace todavía. Bajo volumen actual, no urgente.

#### 9. Bundle "Pack 5 Sobres" — decidido, mismo saco que `mystery-pack`, sin categoría propia
Se queda con la guarda de `cantidad_es_ambigua()` (siempre en `needs_review`) de forma permanente, no como solución provisional — mismo criterio que `mystery-pack` (punto 8): no se crea categoría propia para esto. No reconsiderar aunque aparezca más volumen en el scrape real, salvo que cambie explícitamente esta decisión.

#### 10. Cantidades ambiguas sin resolver: `"x12"`/`"x6"` — `x12` resuelto, `x6` sigue abierto

**Hecho (2026-08-29):** `"x12"` en un `BOOSTER_BOX` deja de ser solo sospechoso -- 12 es EXACTO el multiplicador real de Case para esa categoría (`_CASE_MULTIPLIER_BY_TYPE`, mismo dato que ya usaba `seed_official_catalog.py` para sembrar los canónicos Case). Verificado contra 3 casos reales (Master of Games, OP-14/OP-16/OP-17): los 3 clasifican ahora `BOOSTER_CASE` y auto-confirman contra el canónico ya sembrado, sin necesitar la palabra "case" en el texto. Sobre `name_variant_text`, no tags -- mismo criterio que el resto de señales caras de confirmar mal (Case cuesta ~12x una caja normal). Aplicado también a `PREMIUM_COLLECTION` (`x10`), sin caso real observado todavía que lo confirme.

**Sigue abierto:** `"x6"` en `Starter Deck EX Gear5 [ST21]` (Master of Games) -- `starter-deck` no tiene multiplicador de Case conocido (`_CASE_MULTIPLIER_BY_CATEGORY`/`_CASE_MULTIPLIER_BY_TYPE` no lo incluyen, cero evidencia real de "Starter Deck Case" todavía) y no existe ningún canónico Case de Starter Deck sembrado -- aunque se detectara, no habría contra qué confirmar. Queda en `needs_review` por precaución, igual que antes.

---

### 2026-08-29 — Auditoría completa de `needs_review` (182 → 49 filas)

Sesión de auditoría manual fila a fila contra un CSV exportado de Postgres real (`store_product` en `needs_review`), reproduciendo la lógica exacta de `_evaluate()`/`_best_candidate()` para explicar el motivo concreto de cada una. Ver también los puntos 6 y 10 de arriba (actualizados en esta misma ronda) y `tests/README.md` sección "Bugs reales encontrados" para los dos bugs de composición (regex sin `IGNORECASE`, `&` en las claves del lookup).

#### 11. Lookups nuevos: personaje→código y título de release→código

Dos tablas whitelist nuevas en `shared/classify.py`, mismo espíritu que `_SET_CODE_PREFIXES` (lista blanca explícita, nunca "cualquier texto que suene a"):

- **`_STARTER_DECK_CHARACTER_CODES`** (31 personajes → ST-code): cubre tiendas que nombran el Starter Deck solo por el personaje protagonista, sin color ni código (inGenio BCN, Gameria). Los 4 personajes que Bandai reutilizó en Starter Decks de color distinto (Monkey D. Luffy ×4, Charlotte Katakuri ×2, Yamato ×2, Uta ×2) se dejan FUERA a propósito — sin la palabra de color no hay señal para desambiguar, mejor `needs_review` que un match falso silencioso. Antes de este fix, el candidato sugerido por similitud pura para varios de ellos era directamente incorrecto (ej. "Shanks"/"Buggy"/"Sabo" caían los tres en `ST05`, un Starter Deck genérico sin relación).
- **`_PLAYMAT_CHARACTER_CODES`** (5 personajes → pseudo-código inventado, ej. `SHANKS`/`NAMI`/`ACE`): a diferencia de Starter Deck, Bandai NUNCA asigna código real a los playmats con personaje — son identificadores inventados solo para poder desambiguar dentro de la categoría vía el mismo mecanismo `set_code`. Verificado contra los 18 playmats reales sembrados: ningún personaje se repite, así que ninguna entrada queda ambigua (a diferencia de Starter Deck).
- **`_RELEASE_TITLE_CODES`** (27 títulos oficiales de Bandai → código, ej. "Romance Dawn"→OP01, "The Best vol.2"→PRB02): cubre `BOOSTER_BOX`/`BOOSTER_PACK`/`PREMIUM_COLLECTION` nombrados por su título temático sin código (inGenio BCN). Bandai no repite título de release entre lanzamientos, así que no hay ambigüedad como sí la hay en Starter Deck.
- **`_normalize_for_lookup()`**: decodifica entidades HTML (`&#8217;`, `&amp;`, `&#038;` — vistas crudas, sin decodificar, en varios `raw_name` reales) antes de comparar contra las tablas de arriba.

Impacto medido: inGenio BCN pasó de 36 a 9 filas en `needs_review` (de las cuales 5 son personajes ambiguos dejados fuera a propósito).

#### 12. `Vol.N -> VOLnn` para `PREMIUM_COLLECTION` — backfill de canónicos ya sembrados incluido

`PREMIUM_COLLECTION` no estaba en `_VOLUME_IDENTIFIED_PRODUCT_TYPES` (solo `ILLUSTRATION_BOX`/`PLAYMAT`) pese a que el catálogo oficial de Bandai NUNCA asigna código real a las ediciones "Premium Card Collection -X-" (`data/one_piece_tcg_products.json`: `"code": null` en las 17 variantes) — el "Vol.N" del propio nombre es la única señal real posible. Caso real: Mulligan vendía "Premium Card Collection Vol 3"/"Vol 4" sin ningún candidato con código al que compararse.

Como los canónicos YA sembrados también tenían `set_code=NULL` (nunca se les asignó al sembrarlos, mismo motivo), hizo falta un backfill puntual sobre `cartitas` real además del fix de extracción — 18 productos actualizados. **Colisión conocida y aceptada:** `VOL02` lo comparten 3 sub-líneas distintas ("Best Selection Vol.2", "Live Action Edition vol.2 Baroque Works", "Live Action Edition vol.2 Straw Hat Crew") — `_best_candidate` desempata por `similarity()` DESC como último criterio dentro del mismo `set_code`, así que un texto que sí mencione la sub-línea sigue resolviendo bien; uno genérico ("Premium Card Collection Vol 2" a secas) podría desambiguar mal. Sin caso real observado todavía, no se ha resuelto más a fondo.

#### 13. Código explícito (`PRB-NN`/`DP-NN`) debe pisar el keyword genérico de tipo, no solo cuando `product_type` sigue en `OTROS`

Dos bugs gemelos, mismo patrón de fondo: `_EXTRA_BOOSTER_CODE_RE`/`_DOUBLE_PACK_CODE_RE`/etc. solo ascendían el `product_type` cuando este seguía en `OTROS` — si un keyword genérico (`"caja"`/`"sobre"`, sea del propio `name` o de una `raw_tags` reciclada, ver punto 7 de `tests/README.md`) ya lo había resuelto (mal) a `BOOSTER_BOX`/`BOOSTER_PACK` antes, el código explícito nunca tenía ocasión de corregirlo.

- **PRB-NN** (Saruman Games, `"Premium2 – PRB-02 sobre"`): "Premium2" no coincide con ningún keyword de `PREMIUM_COLLECTION`, así que "sobre" ganaba y el producto quedaba `BOOSTER_PACK` pese al código `PRB-02` explícito.
- **DP-NN** (Pokemillon, `"DP09 The Azure Sea's Seven OP14"`/`"DP-08 Legacy of the Master OP-12"`): `raw_tags` con "Sobre"/"Caja" (metadato de catálogo reciclado, no descripción real del producto) resolvían `BOOSTER_PACK`/`BOOSTER_BOX` antes de que `_DOUBLE_PACK_CODE_RE` tuviera ocasión de ascender a `DOUBLE_PACK`.

Arreglo: ambos códigos ahora pisan `BOOSTER_BOX`/`BOOSTER_PACK` ya resueltos, no solo `OTROS` — PRB es un prefijo reservado en exclusiva a `premium-collection`, DP a `double-pack` (`_SET_CODE_PREFIXES`), así que su presencia es señal más fuerte que un keyword genérico suelto. Impacto: 10 filas reales (3 directas + 7 de arrastre, otros DP-NN con el mismo patrón).

#### 14. Categoría con un único SKU posible en todo el catálogo — auto-confirma sin depender de similitud

`LEARN_DECK`/`DICE_ACCESSORY` tienen exactamente 1 producto canónico sembrado cada una (`"Learn Together Deck Set EN"`, `"Official Dice and Dice Case Set EN"`) — ningún otro candidato posible existe en esas categorías, así que el umbral de similitud/`set_code` no aporta nada, solo bloqueaba un match que ya era inequívoco por construcción. `matcher._single_sku_categories()` (recalculado en cada `run_matching()`, no una lista fija a mano) identifica estas categorías y `_evaluate()` confirma directo cuando `es_fallback=False` y el idioma no contradice explícitamente al único candidato. Impacto: 12 filas (10 `LEARN_DECK` + 2 `DICE_ACCESSORY`).

#### 15. `PrestaShopScraper` generalizado con el fix de nombres truncados de WooCommerce

Distrito Zero (PrestaShop, tema IQIT) truncaba el título en el listado exactamente igual que Arte9 (WooCommerce/Madara) — 19 de 20 `raw_name` en `needs_review` de esa tienda terminaban en `"..."` literal. El mecanismo (`_looks_truncated`/visitar la ficha individual para el `h1` completo) ya existía en `scrapers/woocommerce.py`, documentado como "por patrón, no por tienda", pero nunca se había enganchado en `scrapers/prestashop.py`. Generalizado (reutilizando `_parse_product_detail`'s selector `h1`) y re-scrapeado Distrito Zero en vivo: 0 nombres truncados de 66 productos (antes 20). Impacto: 14 filas.

#### 16. Huecos de catálogo con demanda real de 2+ tiendas — pendiente de decisión, no de código

Encontrados al llegar al fondo de la cola (49 filas): dos productos que NINGUNA tienda podría matchear porque no están en `data/one_piece_tcg_products.json` en absoluto, con demanda confirmada por más de una tienda cada uno —

- **"Premium Card Collection - Uta"**: la venden Pokemillon Y FreakCorp.
- **"One Piece Day 2024/2025 Premium Card Collection"**: la venden Pokemillon Y Golden Pulls.

Exactamente la señal que `matcher.find_missing_canonical_candidates()` está diseñada para detectar. Pendiente: ejecutarla contra la BBDD actual y decidir si se siembran a mano (mismo criterio que el punto 8, `promo-card`). Sin tocar en esta ronda.


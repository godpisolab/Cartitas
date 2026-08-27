# Implementación — Auto-confirmado por `set_code` exacto

Especifica los cambios de código necesarios para lo acordado en la investigación de matching (documentos parte 1-3 + revisión manual de 20 casos), y el set de pruebas centrado en **falsos positivos** — casos reales del CSV que comparten `set_code` pero que el motor debe seguir sin confirmar solo.

**Hecho (2026-08-27):** los 4 cambios de la sección 1 y el set de pruebas de la sección 2 están implementados -- `matcher.py`, `shared/classify.py`, `seed-catalog-app-tcg.sql`, `store_monitor/tests/test_matcher.py` y `store_monitor/tests/test_classify_product.py`. Único punto explícitamente fuera de alcance (ver 1.3): sembrar los canónicos de Case en `seed_official_catalog.py` sigue pendiente.

---

## 1. Cambios a realizar, y por qué cada uno

### 1.1 `_best_candidate()` debe distinguir candidato primario de candidato por fallback cross-categoría

**Por qué:** el fallback cross-categoría (añadido en `4b11bbb` para el caso PRB02) busca por `set_code` en *todo* el catálogo cuando la categoría derivada no tiene nada — es una señal más débil a propósito, pensada para *sugerir*, no para confirmar sola. Sin distinguirlo, el auto-confirmado por `set_code` trataría igual un candidato de su propia categoría que uno encontrado por casualidad de código en una categoría distinta (caso real: una carta promo individual — categoría `promo-card`, vacía — emparejada contra un Booster Pack completo solo porque el código `OP13` aparece mencionado de forma decorativa en el nombre de la carta).

**Cambio:** `_best_candidate()` devuelve también de qué búsqueda vino el resultado (por ejemplo, una tupla con un booleano `es_fallback`, o dos funciones separadas que el llamador combina explícitamente en vez de una sola función que decide sola).

### 1.2 `cantidad_es_ambigua()` — nueva función en `classify.py`

**Por qué:** `set_code` + categoría + idioma coincidentes no bastan si la cantidad/unidad de venta es distinta — un Case de 10-12 cajas, un pack de 5 sobres, o una caja descrita con "x12" en vez de las 24 estándar, no son el mismo SKU que una caja o un sobre suelto, aunque compartan set_code.

**Modelo, verificado contra 6 casos reales del CSV (todos OK):**

```python
# Cantidad estándar de contenido para categorías que SON, por naturaleza,
# un contenedor de varias unidades (una caja SIEMPRE trae N sobres, eso no
# es un bundle, es describir el producto). Solo se marca ambiguo cuando el
# número mencionado DIFIERE del estándar conocido de esa categoría.
_CANTIDAD_ESTANDAR_POR_CATEGORIA = {
    "booster-box": 24,
    "premium-collection": 20,  # verificado contra el catálogo real: "Caja de 20 Sobres PRB02"
}

# Categorías que son, por naturaleza, UNA sola unidad de venta -- cualquier
# cantidad >1 mencionada en el nombre es sospechosa de ser un bundle real
# (Pack 5 Sobres, x6 mazos...), no una descripción de contenido normal.
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

### 1.3 Categoría nueva `booster-case`, y `classify_product()` reconoce "Case" como tipo propio

**Por qué:** en vez de que "Case" se quede para siempre en `needs_review` (guarda barata pero sin salida), se sigue la decisión ya tomada de darle categoría propia — así un Case sí puede llegar a `confirmed` contra su propio canónico, sin arriesgar confundirlo con una Booster Box.

```python
# CLASSIFICATION_RULES -- antes de BOOSTER_BOX (si no, "Case ... Booster
# Box" matchearía como BOOSTER_BOX primero por orden de lista)
("BOOSTER_CASE", ["booster case", "case -", "(case)", "booster box case"]),
```

`PRODUCT_TYPE_TO_CATEGORY_SLUG["BOOSTER_CASE"] = "booster-case"`. La categoría se siembra en `seed-catalog-app-tcg.sql` como hija de `Sellado`, igual que las demás.

**Pendiente de decidir en la propia siembra, no aquí:** `seed_official_catalog.py` necesita generar los canónicos de Case (probablemente derivados de cada Booster Box existente, con el multiplicador de cajas por case que corresponda a cada línea de producto — no todos son 12, `PRB02` era `x10` en el CSV real) — esto es trabajo de siembra, no de `matcher.py`, y se deja fuera del alcance de este documento.

### 1.4 Reestructuración de `promo-card` bajo `single-card`

**Por qué:** una carta promocional individual no es "producto sellado" en el mismo sentido que una caja/sobre — encaja mejor como categoría propia (`single-card`, junto a `Sellado`/`Accesorios` como tercer padre) con `promo-card` como hija, en vez de mezclada bajo `Sellado`.

**Cambio:** `seed-catalog-app-tcg.sql` — nuevo padre `single-card`, mover `promo-card` de hijo de `Sellado` a hijo de `single-card`. No afecta a `classify.py`/`matcher.py` (la categoría sigue siendo `promo-card`, solo cambia su padre en la jerarquía).

### 1.5 `_evaluate()` — la lógica completa con las cinco condiciones

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

# Las CINCO condiciones a la vez: categoría real (ya garantizado si llegamos
# aquí), candidato primario (no fallback), set_code exacto, idioma exacto,
# cantidad no ambigua.
if set_code_matches and not es_fallback and language_matches and cantidad_ok:
    return MatchOutcome("confirmed", product_id, score)

if language_matches and score > CONFIRMED_SIMILARITY_THRESHOLD:
    return MatchOutcome("confirmed", product_id, score)

return MatchOutcome("needs_review", None, None)
```

---

## 2. Set de pruebas — centrado en falsos positivos, con casos reales del CSV

Cada caso de la tabla de "NO debe confirmar" es un producto real de `multi_tienda_one_piece.csv` que comparte `set_code` con un candidato real del catálogo — exactamente el escenario que dispararía un falso positivo si alguna de las cinco condiciones no se comprobara bien.

### 2.1 Control positivo — deben confirmar (ya revisados a mano, sección anterior)

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

### 2.2 Falsos positivos a evitar — el núcleo de este set de pruebas

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
    docs/investigacion-motor-matching-parte3.md."""
    resultado = evaluar(raw_name, raw_variant)
    assert resultado.match_status != "confirmed", f"Falso positivo: {motivo_del_riesgo}"
```

### 2.3 `cantidad_es_ambigua()` — unitarios directos, sin necesitar BBDD

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
    """Verificado contra el CSV real antes de escribirse -- ver
    investigacion-motor-matching-parte3.md sección de propuesta."""
    assert cantidad_es_ambigua(raw_name, category_slug) == esperado
```

### 2.4 `_best_candidate()` — distinción primario/fallback

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

## 3. Resumen de lo que este set de pruebas protege

| Sin esta prueba... | ...este bug pasaría desapercibido |
|---|---|
| 2.2, caso 1 | Una carta suelta se confirmaría como si fuera un producto sellado completo |
| 2.2, casos 2-5 | Un Case/bundle se confirmaría al precio de una unidad suelta — error de precio visible |
| 2.2, casos 6-7 | Un producto JP se confirmaría contra el canónico EN equivocado |
| 2.2, caso 8 | Un lanzamiento futuro se confirmaría contra un set antiguo por similitud de texto engañosa |
| 2.3 | La función de cantidad ambigua podría marcar como sospechoso algo normal (regresión ya vista una vez con mi propia regex durante la investigación) |
| 2.4 | El fallback cross-categoría podría heredar por accidente el mismo nivel de confianza que un match real |

Ningún test de esta lista es hipotético — los ocho de la sección 2.2 son productos reales de tu CSV, encontrados uno a uno durante la investigación, no inventados para la ocasión.

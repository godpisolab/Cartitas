# Propuesta de mejoras — Motor de matching (sesión de revisión sobre datos reales)

Consolida los hallazgos de revisar la cola de 187 `needs_review` real contra el código ya desplegado (`161bae5`, `4f5d83a`, `9b75e37`). Incluye una acción inmediata sin código, dos cambios de código concretos, y una decisión que se deja explícitamente cerrada para no reabrirla sin evidencia nueva.

**Hecho (2026-08-28):** los 4 puntos implementados, más un hallazgo real no cubierto por el documento original. `needs_review` 182→158 (-13%), `confirmed` +20 en la pasada real. Detalle en cada punto más abajo.

---

## 0. Acción inmediata, sin escribir código — hacedla antes que nada de lo de abajo

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

## 1. Detectar `DF-NN` por patrón de código, no por palabra clave de idioma

**Problema:** `"Fruta del Diablo vol.1 [DF-01]"` extrae `set_code=DF01` correctamente, pero `product_type` cae en `OTROS` porque `DEVIL_FRUITS_COLLECTION` solo reconoce la palabra clave en inglés (`"devil fruits collection"`). Afecta a las 3 filas `DF-01`/`DF-02`/`DF-03` vistas en la cola real.

**Por qué esta solución y no "añadir la palabra en español":** verificado contra las 194 líneas del catálogo oficial completo — **ningún otro producto usa el prefijo `DF`**. Es una señal tan fiable como el propio `set_code`, y a diferencia de una palabra clave, no depende de en qué idioma esté escrito el resto del texto — cubre español, catalán, o directamente ninguna palabra descriptiva, sin tener que volver a tocarlo nunca.

```python
# classify.py
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

## 2. Rangos de código (`"ST-15 - ST-20"`, `"[ST-31]~[ST-36]"`) extraen un código equivocado

**Problema, encontrado al revisar la cola completa:** `"One Piece Sobre ST-15 - ST-20 Release Event Pack"` extrae `set_code=ST15` — el primer extremo del rango, como si fuera el código propio del producto. Como la categoría es `booster-pack` (por "Sobre") y ningún sobre lleva prefijo `ST`, esto dispara el fallback cross-categoría y devuelve un Starter Deck real pero completamente ajeno como candidato. Afecta a **6 filas**: `"ST-15 - ST-20 Release Event Pack"` (EN+JP) y `"[ST-31]~[ST-36] Participation/Winner pack"` (2 nombres × 2 idiomas).

**Por qué es un rango y no un código propio:** estos productos son packs promocionales ligados a **todo un lote de mazos** (de ST-15 a ST-20, de ST-31 a ST-36), no a uno solo — el mismo fenómeno que "Tournament Pack"/"Event Pack" (sin código real), solo que aquí la extracción coge un extremo del rango pensando que es un código válido.

```python
# classify.py -- detectar el patrón de rango ANTES de extraer un código suelto
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

## 3. Decisión: NO bajar el umbral de confirmación para categorías sin código (`premium-collection`)

**Motivo de la pregunta:** `"Premium Card Collection Best Selection - Ace, Sabo and Luffy"` encuentra el candidato correcto con score `0.563` — justo por debajo del `0.6` necesario, sin ningún `set_code` al que agarrarse (las colecciones premium no llevan código `OP-NN`/`ST-NN`).

**Decisión, con evidencia delante:** no bajar el umbral para este caso. Verificado que `premium-collection` tiene **22 colecciones distintas** (44 con EN/JP) con nombres muy parecidos entre sí — `"Best Selection"`, `"Best Selection Vol.2"` hasta `"Vol.7"`, `"Leader Collection"`, `"25th Edition"`, `"29th Anniversary Edition"`... En la propia búsqueda de este caso, `"Best Selection"` (0.48) y `"Best Selection Vol.2"` (0.44) aparecen como candidatos alternativos — bajar el umbral lo suficiente para confirmar automáticamente arriesgaría confirmar la colección **equivocada** entre dos casi idénticas, un error peor que dejarlo en revisión manual.

**Vía de mejora real, no immediata:** el catálogo original (`data/one_piece_tcg_products.json`) sí tiene el MSRP oficial de cada colección (`msrp_usd`), nunca cargado a la tabla `product`. Podría servir de señal de apoyo para desambiguar colecciones parecidas — pero necesita validarse antes de confiar en ello (el precio de una tienda no es el MSRP oficial, habría que comprobar si la comparación *relativa* entre colecciones se mantiene fiable). Se deja anotado como investigación futura, no como tarea de este documento.

---

## 4. Recordatorio — sigue pendiente de tu decisión, con más evidencia real esta sesión

`starter-deck` sigue fuera de la lista de categorías con variante JP (`_JP_VARIANT_CATEGORY_SLUGS` solo cubre `booster-box`/`booster-pack`/`booster-case`/`double-pack`/`premium-collection`). Esta sesión aportó **7 ejemplos reales más** de demanda JP en starter deck (`ST-14`, `ST-11`, `ST-09`, `ST-08`, `ST-36`, `ST-34`, `ST-33`, todos de Pokemillon) — la señal de volumen que se dejó como condición para ampliar la lista ya está aquí.

**Hecho (2026-08-28):** confirmado. `starter-deck` añadida a `_JP_VARIANT_CATEGORY_SLUGS` -- 40 canónicos JP nuevos sembrados (`seed_official_catalog.py`).

---

## Resumen de cambios de este documento

| # | Cambio | Tipo | Estado |
|---|---|---|---|
| 0 | Re-ejecutar `run_matching()` en producción | Acción, sin código | Hecho -- premisa corregida (raw_tags), no bastaba solo con re-ejecutar |
| 1 | `DF-NN` clasifica por patrón de código, no por palabra clave | Código + tests | Hecho, generalizado a `DOUBLE_PACK` |
| 2 | Rangos de código (`"NN - NN"`, `"[NN]~[NN]"`) extraen `None` | Código + tests | Hecho -- regex corregida (colisión con carta individual) |
| 3 | No bajar el umbral para categorías sin código | Decisión cerrada, no una tarea | Confirmada, sin cambios |
| 4 | `starter-deck` a la lista de variante JP | Pendiente de tu confirmación | Hecho -- confirmado |

**Resultado real (pasada completa contra producción, 2026-08-28):** `needs_review` 182→158 (-13%), `confirmed` 1069→1089 (+20), `not_applicable` 568→566. 406 tests en `store_monitor` (+11), 201 en `api` (sin cambios este documento).

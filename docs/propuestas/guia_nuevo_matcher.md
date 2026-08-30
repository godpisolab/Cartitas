# Guía de diseño — Nuevo motor de matching (Recognition Pipeline)

**Estado:** diseño validado con un prototipo real (`classify_product_v2`)
ejecutado contra tres conjuntos de datos: los 194 nombres del catálogo
oficial de Bandai + 72 casos reales de `test_classify_product.py` (265
nombres únicos), y **2163 filas reales de producción de 47 tiendas distintas**
(`multi_tienda_one_piece.csv`). **0 regresiones reales en ambos conjuntos**
tras las correcciones documentadas en §10.1. Pendiente de implementar en
`shared/classify.py` y de correr la suite de tests completa (§12).

**Alcance:** sustituye `classify_product()` (shared/shared/classify.py) y la
lógica de scoring de `_best_candidate()`/`_evaluate()` (store_monitor/matcher.py).

**Historial de esta revisión:** tras la primera ronda de diseño (razonamiento
sobre 286 nombres, sin ejecutar código) se construyó un prototipo funcional y
se corrió de verdad contra datos reales — primero contra catálogo+tests
(segunda ronda), después contra el CSV real de 47 tiendas (tercera ronda).
Cada ronda encontró problemas invisibles en la anterior: un bug de diseño que
invalidaba la corrección con más impacto declarado (Corrección 3, §4),
varias regresiones de extracción de código perdidas al reescribir
`classify.py` desde cero, y — solo visible con datos de producción variados —
dos tipos de Fase 0 que perdían su código de acompañamiento y un fallback de
Starter Deck que nunca se había trasladado al diseño nuevo. Además, esta
ronda añade soporte para `raw_tags` del comercio (§4.5), un mecanismo que el
diseño original no contemplaba en absoluto. Todo está corregido y vuelto a
validar — ver §10.1 para el detalle completo de las tres rondas.

---

## 1. Arquitectura general

```
                        SCRAP
                           │
                           ▼
              ┌────────────────────────┐
              │ RECOGNITION PIPELINE   │
              │                        │
              │ F0 Gate                │
              │ F1 Language            │
              │ F2 Product Type        │
              │ F3 Identity Resolution │
              └────────────┬───────────┘
                           │
                           ▼
                    ProductIdentity
                           │
                ┌──────────┴──────────┐
                │                     │
                ▼                     ▼
       Canonical Identity      Search candidates
                │                     │
                └──────────┬──────────┘
                           ▼
                    Evidence Builder
                           │
                           ▼
                    Decision Policy
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          CONFIRMED    NEEDS_REVIEW  UNMATCHED
```

**Separación de responsabilidades respecto al sistema actual:**

| Etapa | Vive en | BBDD | Reemplaza |
|---|---|---|---|
| F0–F3 (Recognition Pipeline) | `shared/shared/classify.py` | No | `classify_product()` actual, `_match_keyword_type()` |
| Canonical Identity / Search candidates | `store_monitor/matcher.py` | Sí | `_best_candidate()` |
| Evidence Builder | `store_monitor/matcher.py` (función nueva) | No (opera sobre datos ya traídos) | parte de `_evaluate()` |
| Decision Policy | `store_monitor/matcher.py` (función nueva) | No | la otra parte de `_evaluate()` |

La mejora clave sobre el diseño actual: hoy `_evaluate()` mezcla "calcular
señales" y "decidir qué hacer con ellas" en la misma función, con 4 caminos
de decisión implícitos entrelazados con comentarios largos. Separar
**Evidence Builder** (construye un objeto con banderas: `exact_name_match`,
`set_code_match`, `language_match`, `packaging_match`, `similarity_score`) de
**Decision Policy** (tabla de decisión pura sobre esas banderas) hace que la
política de decisión sea testeable sin BBDD y auditable de un vistazo.

**Principio de diseño transversal, añadido tras esta ronda de revisión:**
ninguna condición del pipeline debe basarse en "¿se encontró algo?" cuando lo
que hay que comprobar es "¿se encontró lo que ESTA família busca?". Una tabla
de lookup compartida entre varias familias, comprobada con un simple `is not
None`, dice "esta tabla reconoce el texto" — no "el texto pertenece a esta
família". Ver §10.1 para el bug real que costó no aplicar este principio
desde el principio, y §7 para cómo queda resuelto (tablas exclusivas por
família en vez de una tabla compartida con filtro a posteriori).

---

## 2. Fase 0 — Gate

Corta el pipeline entero. Nada de lo que sigue se evalúa si esto dispara.
`PROMO_CARD`, `MYSTERY_PACK` y `DICE_ACCESSORY` se suben aquí junto a
`LOTE_CARTAS` — las cuatro comparten la misma naturaleza: unidades/lotes
individuales o accesorios sin producto sellado canónico equivalente contra el
que comparar precio.

```
1. LOTE_CARTAS   (CGC/PSA/BGS) | "lote"
                 → not_applicable, fin.

2. PROMO_CARD    "promo card" | "carta promo" | "carta promocional"
                 | "promo pack" | "promotion pack" | " P-NNN"
                 | "don!!" SIN contexto de sellado alrededor
                   (sin "set"|"pack"|"caja"|"box"|"sobre(s)" cerca)
                 → not_applicable, fin.

3. MYSTERY_PACK  "mystery pack" | "mystery box"
                 → not_applicable, fin.

4. DICE_ACCESSORY "dice"
                 → not_applicable, fin.
```

**Corrección 1 — `"lote"` en `LOTE_CARTAS`.** El sistema actual también
captura lotes de cartas sueltas sin gradear (`"Lote 50 cartas sueltas One
Piece"`, con test dedicado), no solo cartas gradeadas CGC/PSA/BGS.

**Corrección 2 — excepción "Don!!" portada al gate de `PROMO_CARD`.**
Distingue la carta promocional suelta de regalo que viene *dentro* de un
Double Pack (`"Don!! (DP10 Map)..."` → `PROMO_CARD`) de un Double Pack
realmente sellado que solo menciona "Don!!" en su descripción
(`"Special DON!! Card Pack DP-06"` → sí es `DOUBLE_PACK`, porque trae "Pack"
cerca):

```python
_DON_CARD_RE = re.compile(r"\bdon!!", re.IGNORECASE)
_SEALED_PRODUCT_CONTEXT_RE = re.compile(
    r"\bset\b|\bpack\b|\bcaja\b|\bbox\b|\bsobres?\b", re.IGNORECASE)

if _DON_CARD_RE.search(text) and not _SEALED_PRODUCT_CONTEXT_RE.search(text):
    return Classification("PROMO_CARD", None, language, None)
```

**Actualización de esta ronda — el código SÍ se conserva, aunque
`not_applicable`.** La versión anterior de esta guía dejaba `set_code=None`
para el caso "Don!!" razonando que, al ser `not_applicable`, nunca llega a
Fase 3 a buscar candidato y el código "no hace falta". Decisión revisada:
sigue sin usarse para comparar precio, pero es información real y barata de
conservar (de qué Double Pack viene la carta de regalo) — se extrae el
`DP-NN` del propio texto si está presente:

```python
if _DON_CARD_RE.search(text) and not _SEALED_PRODUCT_CONTEXT_RE.search(text):
    dp_match = _DOUBLE_PACK_SET_CODE_RE.search(text)
    code = f"DP{int(dp_match.group(1)):02d}" if dp_match else None
    return Classification("PROMO_CARD", code, language, None)
```

**Corrección 6 (NUEVA, encontrada con el CSV real de 47 tiendas) —
`LOTE_CARTAS` y `PROMO_CARD` también conservan un código de acompañamiento,
generalizando el mismo principio que "Don!!".** La versión anterior de esta
guía asumía que `set_code` debía quedar siempre en `None` para toda la Fase 0
por ser `not_applicable`. Comprobado contra 2163 filas reales: el sistema
actual SÍ conserva el set/expansión de origen de una carta gradeada
(`"Monkey.D.Luffy (OP05-119) Unnumbered Promos"` + variante `"CGC 10"` →
`LOTE_CARTAS`/`OP05`) y de una carta promo sellada con contexto de set — 69 y
1 casos reales respectivamente. Es información barata y útil (de qué
expansión viene la carta) aunque, igual que "Don!!", nunca se use para
comparar precio. Se extrae con el mismo regex genérico que usa el sistema
actual para cualquier tipo sin tratamiento especial (`_SET_CODE_PREFIXES`):

```python
_GENERIC_CODE_RE = re.compile(
    rf"\b({'|'.join(_SET_CODE_PREFIXES)})[\s-]?0*(\d{{1,3}})\b", re.IGNORECASE)

def _extract_generic_code(name):
    m = _GENERIC_CODE_RE.search(name)
    return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else None
```

`MYSTERY_PACK` y `DICE_ACCESSORY` NO reciben este tratamiento — comprobado
contra las 2163 filas reales, ninguna de las dos tiene nunca un código real
que conservar (0 casos con `set_code` no vacío). `main_set` sigue en `None`
para las cuatro família de Fase 0 — solo `DOUBLE_PACK` en Fase 2 deriva
`main_set` de su `set_code` (§4.2).

---

## 3. Fase 1 — Idioma

Independiente del tipo de producto, no bloquea la Fase 2.

```
EN / JP / KR / ES  sobre  name + variant
```

Sin cambios respecto al detector actual (`_detect_language`), incluida la
asunción por defecto a EN cuando no hay señal contraria y no aparece
"non-english"/"non english". **Aclarado en esta ronda:** las tags
(`extra_type_hint`, §4.5) NUNCA participan en la detección de idioma, ni en
el sistema actual ni en el nuevo — solo afectan a la Fase 0/Fase 2
(determinación de tipo).

---

## 4. Fase 2 — Tipo de producto

**El orden es la parte crítica del diseño: primer match gana.** Cada família
devuelve, cuando aplica, su `packaging` (Display / Case / Sobre) calculado
por un detector genérico compartido.

```
 1. DEVIL_FRUITS_COLLECTION  DF-NN | "devil fruits collection" | "fruta del diablo"
       └─ set_code: DF-NN directo, o Vol.N → DFNN si no hay código explícito
 2. ILLUSTRATION_BOX         IB-NN | "illustration box vol.N" | "illustration box"
       └─ set_code: Vol.N, o IB-NN → VOLNN si no hay "Vol." en el texto
 3. PLAYMAT                  "playmat" | "tapete"
       └─ set_code: Vol.N si existe, si no tabla de personajes (_PLAYMAT_CHARACTER_CODES)
 4. SLEEVES                  "sleeve" | "funda"  (+ Vol.N si aparece, sin sub-línea)
 5. PREMIUM_CARD_COLLECTION  "premium card collection"
       └─ set_code: Vol.N primero, tabla PROPIA de líneas si no hay Vol.N
                    (NUNCA la tabla de OP/PRB/EB, ver Corrección 4 más abajo)
 6. STARTER_DECK             "learn together"|"learn to play"|"aprende a jugar"  ← PRIMERO dentro de esta rama
                              | ST-NN | "starter deck" | "ultra deck" | "mazo"
       └─ set_code: ST-NN (con separador " " o "-"), tabla de TÍTULOS propia,
                    tabla de personajes como último recurso
       └─ packaging: Display (xNN | "display") | Sobre (default)
 7. DOUBLE_PACK              DP-NN | "double pack" | "doble pack"
       └─ set_code: DP-NN directo, "Double Pack Set N"/"Vol N"/"Vol.N" si no,
                    o derivado de OP-NN vía tabla DP↔OP
       └─ main_set: derivado de set_code vía tabla DP↔OP (nuevo, ver Corrección 5)
       └─ packaging: Display (x10 | "display") | Sobre (default)
 8. PREMIUM_BOOSTER_BOX      PRB-NN | título de esta línea (tabla PROPIA, no compartida)
       └─ packaging: Display (x20|"display"|"booster box"|"caja de sobres"|"caja sobre")
                    | Case    (x10 | "case")
                    | Sobre   ("booster pack" | "sobre")  ← default
 9. ONE_PIECE (booster ppal) ninguna de las 1-8 + OP-NN en el nombre
                              | título de esta línea (tabla PROPIA)
       └─ packaging: Display (x24|"display"|"booster box"|"caja de sobres"|"caja sobres")
                    | Case    (x12 | "case")
                    | Sobre   ("booster pack" | "sobre")  ← default
10. EXTRA_BOOSTER            EB-NN | "extra booster" | título de esta línea (tabla PROPIA)
       └─ packaging: Display (x24|"display"|"booster box"|"caja de sobres"|"caja sobres")
                    | Case    (x12 | "case")
                    | Sobre   ("booster pack" | "sobre")  ← default
11. Catch-all ONE_PIECE      "booster" | "caja" | "box" | "sobre(s)" -- SIN código
                              ni título de ninguna família anterior (Corrección 10,
                              cierra §11.4, decisión de producto 2026-08-30)
       └─ set_code / main_set: None (no hay señal de qué lanzamiento es)
       └─ packaging: igual que ONE_PIECE (paso 9)

    → si nada de lo anterior matchea: OTROS
```

*(`PROMO_CARD`, `MYSTERY_PACK`, `DICE_ACCESSORY` ya no aparecen aquí — se
resuelven en Fase 0, ver §2.)*

**⚠️ Corrección 3 — `ONE_PIECE` necesita el mismo fallback por título que ya
tienen `EXTRA_BOOSTER`/`PREMIUM_BOOSTER_BOX`.** Un booster nombrado solo por
su título temático ("Booster Pack: Romance Dawn", sin ningún `OP-NN`) debe
seguir resolviendo a `ONE_PIECE`/OP01. Confirmado con datos: los 17 boosters
principales del catálogo oficial se nombran así.

**⚠️ Corrección 4 (NUEVA, encontrada al ejecutar el prototipo contra datos
reales — no era visible solo razonando sobre el diseño) — la tabla de
títulos usada por la Corrección 3 NO puede estar compartida entre familias.**
La primera versión de esta guía reutilizaba una única tabla
`_RELEASE_TITLE_CODES` con títulos de OP, PRB y EB mezclados, y cada família
comprobaba solo "¿aparece algún título de la tabla?" (`is not None`). Como
`PREMIUM_BOOSTER_BOX` se evalúa antes que `ONE_PIECE` en el orden de arriba,
y la tabla compartida SÍ contiene "romance dawn" (apuntando a OP01),
`"Booster Pack: Romance Dawn"` disparaba la condición de `PREMIUM_BOOSTER_BOX`
antes de que `ONE_PIECE` tuviera ocasión de evaluarlo — **exactamente los 17
boosters que la Corrección 3 pretendía arreglar quedaban mal clasificados**.
Un caso real más grave: `"One Piece Card Game Memorial Collection Eb-02
Sobre"` trae el código explícito `EB-02` en el texto, pero como la extracción
de `PREMIUM_BOOSTER_BOX` caía al lookup de título antes de comprobar otros
prefijos, el resultado era `EB01` (el código de "memorial collection" en la
tabla) — un dato erróneo, no solo una família equivocada.

Reordenar los pasos (comprobar `ONE_PIECE` antes que `PREMIUM_BOOSTER_BOX`)
**no resuelve el problema, solo lo traslada a la dirección contraria** —
comprobado con datos: con ese reorden, `"Premium Booster: One Piece Card The
Best"` (un PRB real) pasaba a clasificarse como `ONE_PIECE`. La causa no es
el orden, es que la condición de entrada pregunta "¿hay ALGÚN título en la
tabla?" en vez de "¿hay un título de MI família?".

**Corrección aplicada: separar la tabla en tres tablas exclusivas, una por
línea** (`_OP_TITLE_CODES`, `_PRB_TITLE_CODES`, `_EB_TITLE_CODES`, ver §7).
Cada família solo puede reconocer sus propios títulos — el orden entre pasos
deja de importar para este problema, porque ya no hay nada que cruzar. El
mismo criterio se aplicó a `PREMIUM_CARD_COLLECTION` (§4.3) y motivó
construirle su propia tabla de nombres de línea, nunca la de OP/PRB/EB (esa
família no es una "línea de expansión", es un producto aparte).

**Playmat — variante "tapete" recuperada.** El sistema actual reconoce
`"tapete"` (español) además de `"playmat"` — se había perdido al escribir el
primer borrador de esta guía. Corregido: `if "playmat" in text or "tapete"
in text`.

**Sleeves — singular, no solo plural.** El catálogo oficial nombra la
mayoría de sus productos de fundas en singular (`"Limited Card Sleeve"`,
`"Official Card Sleeve..."`), no en plural. Comprobar solo `"sleeves"`/
`"fundas"` (plural) dejaba sin clasificar 21 de 194 productos reales del
catálogo. Corregido a la raíz sin plural: `"sleeve" in text or "funda" in
text` (cubre ambas formas, ya que "sleeve" es substring de "sleeves").

### 4.1 Por qué "learn together" va *dentro* de `STARTER_DECK`, y primero

Dato real encontrado en el propio código actual (comentario sobre un caso de
producción, Arte9): el raw_name **`"LEARN TOGETHER DECK SET – STARTER DECKS
ONE PIECE"`** contiene *ambas* keywords ("learn together" y "starter decks")
a la vez. Por eso no basta con decidir el orden entre familias — dentro de la
propia rama `STARTER_DECK`, hay que comprobar primero el patrón "learn
together / learn to play / aprende a jugar" y solo si no aparece, caer al
patrón genérico `ST-NN | "starter deck"`.

### 4.2 Tabla DP↔OP y main_set de Double Pack

Vive hardcodeada en `classify.py` — decisión de arquitectura: el propio
módulo declara *"sin red, sin BBDD"*.

```python
_DP_TO_OP: dict[str, str] = {
    "DP01": "OP04", "DP02": "OP05", "DP03": "OP06", "DP04": "OP07",
    "DP05": "OP08", "DP06": "OP09", "DP07": "OP11", "DP08": "OP12",
    "DP09": "OP13", "DP10": "OP15", "DP11": "OP16", "DP12": "OP17",
}
```

**Corrección 5 (NUEVA) — `main_set` de Double Pack ahora se deriva de esta
tabla.** La versión anterior de esta guía dejaba `main_set=None` siempre para
`DOUBLE_PACK`. Una vez identificado el `DP-NN` (por código explícito o por el
fallback de número escrito, ver abajo), su `main_set` se rellena vía
`_DP_TO_OP.get(dp_code)` — información gratis que ya teníamos y no se estaba
usando.

**Fallback de número escrito (recuperado, se había perdido en el primer
borrador).** El catálogo oficial nombra sus Double Pack como `"Double Pack
Set Vol.N"` — sin `DP-NN` en el nombre. El sistema actual ya cubre la
variante `"Double Pack Set N"` (sin "Vol."); ampliado aquí para cubrir
también `"Double Pack ... Vol.N"`:

```python
_DOUBLE_PACK_SET_NUM_RE = re.compile(r"\bdouble pack set\s*0*(\d{1,3})\b", re.IGNORECASE)
_DOUBLE_PACK_VOL_NUM_RE = re.compile(r"\bdouble pack.*?vol\.?\s*0*(\d{1,3})\b", re.IGNORECASE)

def _extract_dp_code_and_main_set(name, text):
    """Orden: código explícito DP-NN primero; si no está, número escrito
    ("Set N" / "Vol N" / "Vol.N"). Una vez ubicado el DP-NN por cualquiera
    de las dos vías, main_set se deriva de _DP_TO_OP -- nunca al revés (un
    OP decorativo en el texto no es el identificador real del Double Pack).
    Guarda de rango al principio (Corrección 9, §7) -- un "DP-NN - DP-MM"
    no tiene código propio, y sin dp_code main_set sale None solo, vía
    _DP_TO_OP.get(None)."""
    if _RANGO_CODIGOS_RE.search(name):
        return None, None
    m = _DOUBLE_PACK_SET_CODE_RE.search(name)
    dp_code = f"DP{int(m.group(1)):02d}" if m else None
    if dp_code is None:
        m = _DOUBLE_PACK_SET_NUM_RE.search(text) or _DOUBLE_PACK_VOL_NUM_RE.search(text)
        dp_code = f"DP{int(m.group(1)):02d}" if m else None
    main_set = _DP_TO_OP.get(dp_code) if dp_code else None
    return dp_code, main_set
```

Validado contra los 12 Double Pack reales del catálogo oficial (nombrados
`"Double Pack Set Vol.1"`...`"Vol.12"`, sin código abreviado) — los 12
resuelven ahora su `DP-NN` y su `main_set` correctamente.

### 4.3 Premium Card Collection — família aparte, tabla propia

**No es una línea de expansión como OP/PRB/EB, es una família distinta con
sus propios nombres de edición** (mismo criterio que Playmat con su tabla de
personajes). Verificado contra las 17 entradas reales del catálogo oficial:
ninguna tiene `code` asignado por Bandai — identificador inventado a
propósito, igual que en Playmat/Starter Deck.

```python
_PREMIUM_CARD_COLLECTION_EDITION_CODES: tuple[tuple[str, str], ...] = (
    ("25th edition", "ED25TH"),
    ("film red edition", "EDFILMRED"),
    ("best selection", "EDBEST"),       # sin Vol. -> edición base de la sublínea
    ("live action edition", "EDLIVEACTION"),  # solo la base sin Vol., ver nota
    ("bandai card games fest", "EDFEST2324"),
    ("leader collection", "EDLEADER"),
    ("29th anniversary edition", "ED29TH"),
    ("ace sabo luffy", "EDACESABOLUFFY"),
)
```

Se comprueba **Vol.N primero** (identificador real cuando existe) y esta
tabla solo como fallback cuando no hay Vol.N — las entradas con Vol.N
("Best Selection Vol.2..7", "6 assort vol.1") no necesitan estar aquí,
`_extract_volume_code` ya las resuelve.

**Nota pendiente sin resolver:** `"Live Action Edition vol.2 Baroque Works"`
y `"...vol.2 Straw Hat Crew"` son dos productos reales distintos que
comparten el mismo `Vol.2` — el identificador Vol.N solo no basta para
distinguirlos entre sí. Ver §11.

### 4.4 Starter Deck — tabla de títulos completos (nueva)

Igual que OP/PRB/EB tienen su tabla de títulos temáticos, Starter Deck la
necesita también — construida y verificada contra las 33 entradas ST reales
del catálogo oficial (excluidas variantes "PRE"):

```python
_ST_TITLE_CODES: tuple[tuple[str, str], ...] = (
    ("straw hat crew", "ST01"), ("worst generation", "ST02"),
    ("the seven warlords of the sea", "ST03"), ("animal kingdom pirates", "ST04"),
    ("one piece film edition", "ST05"), ("absolute justice", "ST06"),
    ("big mom pirates", "ST07"), ("the three captains", "ST10"),
    ("zoro and sanji", "ST12"), ("the three brothers", "ST13"),
    ("3d2y", "ST14"), ("red edward newgate", "ST15"), ("green uta", "ST16"),
    ("blue donquixote doflamingo", "ST17"), ("purple monkey d luffy", "ST18"),
    ("black smoker", "ST19"), ("yellow charlotte katakuri", "ST20"),
    ("gear 5", "ST21"), ("ace newgate", "ST22"), ("red shanks", "ST23"),
    ("green jewelry bonney", "ST24"), ("blue buggy", "ST25"),
    ("purple black monkey d luffy", "ST26"), ("black marshall d teach", "ST27"),
    ("green yellow yamato", "ST28"), ("egghead", "ST29"), ("luffy ace", "ST30"),
    ("red monkey d luffy", "ST31"), ("green roronoa zoro", "ST32"),
    ("blue kuzan", "ST33"), ("purple charlotte katakuri", "ST34"),
    ("red black sabo", "ST35"), ("yellow eustass captain kid", "ST36"),
)
```

**Deliberadamente fuera de esta tabla:** ST-08 ("Monkey D. Luffy" a secas),
ST-09 ("Yamato" a secas), ST-11 ("Uta" a secas) — personajes reutilizados sin
cualificador de color en el nombre oficial, siguen siendo ambiguos igual que
en `_STARTER_DECK_CHARACTER_CODES` (no se puede saber cuál de los 4 decks de
Luffy es sin más información). Las variantes CON color (`"Green Uta"`,
`"Purple Monkey.D.Luffy"`, etc.) SÍ se incluyen porque el color ya las
distingue sin ambigüedad — comprobado que ninguna clave de la tabla es
substring de otra.

**Corrección de regex — separador de espacio en el código ST.** El código
actual en producción reconoce `"ST 36"` (con espacio) además de `"ST-36"`
(con guion) vía `[\s-]?` en su regex genérico. El primer borrador de esta
guía usaba `-?` (solo guion), perdiendo esa variante real (Arte9). Corregido:

```python
_ST_CODE_RE = re.compile(r"\bST[\s-]?0*(\d{1,3})\b", re.IGNORECASE)
```

**Corrección 7 (NUEVA, encontrada con el CSV real) — fallback de número
suelto sin "ST" en ningún sitio.** El sistema actual reconoce `"Starter Deck
23"` / `"Uta Starter Deck 16"` — el número va pegado a la frase "Starter
Deck", sin ninguna letra "ST" delante (`_STARTER_DECK_NUM_RE`). Se había
perdido por completo en el primer borrador de esta guía. 12 filas reales del
CSV lo necesitan, las 12 de la misma tienda (Gameria):

```python
_STARTER_DECK_NUM_RE = re.compile(r"\bstarter deck\s*0*(\d{1,2})\b", re.IGNORECASE)
```

Orden de `_extract_st_code()` actualizado: código `ST-NN` explícito primero,
luego este fallback de número suelto, luego la tabla de títulos (§7), y solo
al final la tabla de personajes como último recurso.

**Investigado y descartado — mapeo ST→OP para `main_set`.** Se consideró
aplicar a Starter Deck el mismo tratamiento que a Double Pack (derivar
`main_set` de un mapeo fijo a un booster OP). Comprobado contra las fechas de
lanzamiento reales del catálogo oficial: a diferencia de Double Pack (siempre
1 DP por 1 OP, misma fecha exacta), los Starter Deck se lanzan en tandas de
hasta 6 el mismo día sin que haya ningún OP asociado esa fecha (ej.
2026-07-31: 6 Starter Decks, ST31-ST36, sin booster OP ese día). **No hay una
relación 1:1 que mapear con los datos disponibles.** Se deja pendiente por si
existe otra fuente (agrupación oficial de Bandai por "serie") no contemplada
aquí — ver §11.

### 4.5 Tags del comercio (`extra_type_hint`) como respaldo — NUEVO

**Hueco de diseño no contemplado hasta esta ronda.** El sistema actual usa un
tercer parámetro, `extra_type_hint` (en la práctica, `raw_tags` de Shopify),
como señal de tipo adicional cuando `name`+`variant` no bastan — documentado
en `TestExtraTypeHintDeTags` (`test_classify_product.py`) con 10 tests reales
que fijan el comportamiento exacto. El diseño original de esta guía no
mencionaba `extra_type_hint` en ningún sitio.

**Principio del sistema actual, replicado tal cual:** `name`+`variant` se
prueban primero, SIN tags. Las tags solo entran como respaldo si eso da
`OTROS` — **nunca pueden pisar una clasificación que `name`+`variant` ya
resuelven por sí solos.** El motivo, según el propio historial de
`classify.py`: las tags son metadato de catálogo del comercio, a menudo
reciclado entre productos sin relación real (`raw_tags` de un Illustration
Box conteniendo `"PRB-02 The Best vol. 2"` de otro producto cualquiera).

```python
def _classify_pass(name, variant_title, tags):
    """Una pasada del pipeline sobre name+variant(+tags opcional)."""
    text = f"{name} {variant_title or ''} {tags or ''}".lower()
    # ... Fase 0 + Fase 2 completas, igual que en §8 ...


def classify_product_v2(name, variant_title=None, extra_type_hint=None):
    if not name:
        return Classification("OTROS", None, None, None)

    sin_tags = _classify_pass(name, variant_title, tags=None)
    if sin_tags.product_type != "OTROS" or not extra_type_hint:
        return sin_tags

    return _classify_pass(name, variant_title, tags=extra_type_hint)
```

**Hallazgo importante al validar contra los 10 tests reales del sistema
actual:** en el sistema actual, las tags eran necesarias para resolver ~35%
del catálogo real (`"One Piece OP13 Carrying On His Will"` sin tags →
`OTROS`, con tags "Caja..." → `BOOSTER_BOX`). En el pipeline nuevo, **la
mayoría de esos casos ya no necesitan tags en absoluto** — la Corrección 3/4
(comprobar el código OP/DP/PRB/EB directamente, sin exigir antes una keyword
de tipo) resuelve en la primera pasada lo que antes solo resolvían las tags.
Verificado con los 8 tests de código real de `TestExtraTypeHintDeTags`
replicados contra `classify_product_v2` — los 8 dan el resultado correcto,
la mayoría **sin necesitar la segunda pasada con tags en absoluto** (el
código explícito en `name` ya resuelve la família antes de mirar las tags).

**A diferencia del sistema actual, aquí no hace falta ningún paso de
"override" adicional** (el sistema actual necesita comprobaciones específicas
para que un código DP-NN/PRB-NN explícito en el nombre gane a una tag
genérica o reciclada de "caja"/"sobre" ya resuelta). El diseño de dos pasadas
con corte temprano (si `name`+`variant` ya resuelven algo, las tags ni se
miran) hace estas protecciones automáticas: no puede haber "override" de
tags sobre algo que nunca llegó a intentarse con tags.

**Relación con §11.4 (ya resuelto, decisión de producto 2026-08-30):** se
mantiene el catch-all genérico (Corrección 10, §4 paso 11) — mismo
comportamiento que el sistema actual. El caso donde las tags siguen siendo
genuinamente necesarias es un nombre sin código, sin título reconocible y
SIN NINGUNA de las cuatro keywords de sellado (`_GENERIC_SEALED_CATCHALL_RE`)
tampoco en `name`+`variant` — ahí las tags (`raw_tags`) siguen siendo la
única señal disponible para no perder el producto a `OTROS`, exactamente
igual que en el sistema actual.

---

## 5. `Classification` — campo nuevo

```python
# shared/shared/domain.py
@dataclass
class Classification:
    product_type: str
    set_code: Optional[str]
    language: Optional[str]
    main_set: Optional[str]
    packaging: Optional[str] = None  # "display" | "case" | "sobre" | None
```

`packaging` pasa de ser un cálculo aparte (`is_box_variant()`, llamado
condicionalmente solo desde `matcher.py`) a ser parte del resultado de
primera clase de la clasificación. Se calcula dentro de la Fase 2, en las
família que lo necesitan.

---

## 6. Detector de packaging genérico

```python
_DISPLAY_RE = re.compile(r"\bdisplay\b", re.IGNORECASE)
_CASE_KEYWORD_RE = re.compile(r"\bcase\b", re.IGNORECASE)
_SOBRE_RE = re.compile(r"\bbooster pack\b|\bsobre\b", re.IGNORECASE)
_QTY_RE = re.compile(r"\bx\s*(\d+)\b", re.IGNORECASE)

_PACKAGING_UNITS = {
    "STARTER_DECK":        {"display": 6},
    "DOUBLE_PACK":         {"display": 10},
    "PREMIUM_BOOSTER_BOX": {"display": 20, "case": 10},
    "ONE_PIECE":           {"display": 24, "case": 12},
    "EXTRA_BOOSTER":       {"display": 24, "case": 12},
}

def _qty_matches(text, expected):
    if expected is None:
        return False
    m = _QTY_RE.search(text)
    return bool(m) and int(m.group(1)) == expected

def _detect_packaging(family, text):
    """Sobre = default si nada más matchea (decisión explícita, ver §4)."""
    units = _PACKAGING_UNITS.get(family, {})
    if _DISPLAY_RE.search(text) or _qty_matches(text, units.get("display")):
        return "display"
    if _CASE_KEYWORD_RE.search(text) or _qty_matches(text, units.get("case")):
        return "case"
    if _SOBRE_RE.search(text):
        return "sobre"
    return "sobre"
```

Sin cambios respecto a la versión anterior de esta guía — este detector no
se vio afectado por los bugs encontrados en la ronda de validación.

---

## 7. Funciones de extracción de código (`_extract_*_code`)

**Cambio de diseño principal de esta ronda:** la antigua tabla única
`_RELEASE_TITLE_CODES` (compartida entre OP/PRB/EB) se elimina por completo
y se sustituye por tres tablas exclusivas. Esto hace innecesario cualquier
filtro de prefijo a posteriori — cada tabla, por construcción, solo puede
devolver códigos de su propia família.

```python
_OP_TITLE_CODES: tuple[tuple[str, str], ...] = (
    ("romance dawn", "OP01"), ("paramount war", "OP02"),
    ("pillars of strength", "OP03"), ("pillard of strength", "OP03"),
    ("kingdoms of intrigue", "OP04"), ("awakening of the new era", "OP05"),
    ("wings of the captain", "OP06"), ("500 years in the future", "OP07"),
    ("500 years into the future", "OP07"), ("two legends", "OP08"),
    ("emperors in the new world", "OP09"), ("royal blood", "OP10"),
    ("a fist of divine speed", "OP11"), ("a fist divine speed", "OP11"),
    ("legacy of the master", "OP12"), ("legacy of the masters", "OP12"),
    ("carrying on his will", "OP13"), ("the azure seas seven", "OP14"),
    ("adventure on kamis island", "OP15"), ("the time of battle", "OP16"),
    ("the worlds strongest warriors", "OP17"),
)

_PRB_TITLE_CODES: tuple[tuple[str, str], ...] = (
    ("the best 2", "PRB02"), ("the best vol 2", "PRB02"),
    ("the best vol.2", "PRB02"), ("the best", "PRB01"),
)

_EB_TITLE_CODES: tuple[tuple[str, str], ...] = (
    ("heroines edition vol 2", "EB05"), ("heroines edition vol.2", "EB05"),
    ("heroines edition", "EB03"), ("memorial collection", "EB01"),
    ("anime 25th collection", "EB02"),
)
```

`_ST_TITLE_CODES` y `_PREMIUM_CARD_COLLECTION_EDITION_CODES` están en §4.4 y
§4.3 respectivamente — mismo criterio, tablas exclusivas por família.

```python
_DF_CODE_RE = re.compile(r"\bDF[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)
_IB_CODE_RE = re.compile(r"\bIB[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)
_ST_CODE_RE = re.compile(r"\bST[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)  # espacio o guion, ver §4.4
_PRB_CODE_RE = re.compile(r"\bPRB[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)
_OP_CODE_RE = re.compile(r"\bOP-?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)
_EB_CODE_RE = re.compile(r"\bEB[\s-]?0*(\d{1,3})\b(?!-\d)", re.IGNORECASE)

# Catch-all de última instancia (Corrección 10, §4 paso 11, cierra §11.4) --
# mismas cuatro keywords que CLASSIFICATION_RULES usa hoy para BOOSTER_BOX/
# BOOSTER_PACK genéricos ("booster box", "caja de sobres", "caja", "booster",
# "sobre "), unificadas en una sola regex con límites de palabra. Se evalúa
# DESPUÉS de las 10 família anteriores en _classify_pass (§8) -- para cuando
# se llega aquí, ya se descartó que el texto pertenezca a Starter Deck/
# Playmat/Sleeves/Double Pack/etc, así que "caja"/"booster"/"sobre" sueltos
# ya no pueden colisionar con esas família (todas tienen su propia keyword
# más específica, evaluada antes).
_GENERIC_SEALED_CATCHALL_RE = re.compile(r"\bbooster\b|\bcaja\b|\bbox\b|\bsobres?\b", re.IGNORECASE)
```

**Corrección 8 (NUEVA, encontrada al contrastar el prototipo contra la suite
real `test_classify_product.py`, no solo contra los 265+2163 nombres sueltos
-- ver §10.1 tercera ronda, nota final) -- `(?!-\d)` en las seis regex de
família de Fase 2.** Sin esto, un código de família aparece también como
prefijo de la numeración de una CARTA INDIVIDUAL dentro de otro producto
(`"CHOPPER's Vol.1 carta/comic promocional EB02-003"`, `test_classify_product.py`
caso `codigo-eb-de-CARTA-INDIVIDUAL-eb02-003-no-dispara-el-fallback-de-booster`)
y la família se auto-activa por error (`EB02-003` → `EXTRA_BOOSTER`, cuando
debe quedar `OTROS`). El sistema actual ya evita esto en sus regex de tipo
(`_EXTRA_BOOSTER_CODE_RE`, `_DOUBLE_PACK_CODE_RE`, `_PREMIUM_BOOSTER_CODE_RE`,
todas con `(?!-\d)`) -- se generaliza aquí a las seis família de Fase 2 (antes
solo EB/DP/PRB lo tenían) porque el riesgo es el mismo para cualquier prefijo.

**`_GENERIC_CODE_RE` (§2, Corrección 6) NO lleva este lookahead, a
propósito.** `test_codigo_de_carta_individual_no_se_confunde_con_rango`
exige que `"Rebecca (OP10-058) (V.1) Royal Blood (Non-English)"` + variante
`"CGC 10"` conserve `set_code == "OP10"` -- ese caso es `LOTE_CARTAS` (Fase
0, vía CGC) y usa `_extract_generic_code()`, que necesita seguir siendo
permisivo para capturar el set de acompañamiento de una carta suelta
gradeada. Añadir el lookahead ahí rompería ese test en la dirección
contraria. Dos regex con propósitos distintos: la de família (Fase 2, "¿es
ESTE el producto sellado?") exige el lookahead; la genérica de
acompañamiento (Fase 0, "¿de qué set viene esta carta suelta?") no lo
admite.

```python
def _extract_df_code(name):
    if _RANGO_CODIGOS_RE.search(name):
        # Ver Corrección 9 más abajo -- un rango no tiene código propio.
        return None
    m = _DF_CODE_RE.search(name)
    if m:
        return f"DF{int(m.group(1)):02d}"
    # Fallback Vol.N -> DFNN (recuperado, se había perdido en el primer
    # borrador -- verificado que Vol.N y el código DF de esta família son
    # el mismo número, ej. "Vol.3 Op-Op Fruit (DF03)").
    return _extract_volume_code(name, "DF")


def _extract_volume_code(name, prefix):
    m = _VOLUME_RE.search(name)
    return f"{prefix}{int(m.group(1)):02d}" if m else None


def _extract_illustration_box_code(name):
    code = _extract_volume_code(name, "VOL")
    if code is None:
        # Convención alternativa IB-NN sin "Vol." en el texto (recuperado,
        # 56 store_product reales afectados según el propio historial de
        # classify.py cuando se corrigió la primera vez).
        if _RANGO_CODIGOS_RE.search(name):
            return None
        m = _IB_CODE_RE.search(name)
        code = f"VOL{int(m.group(1)):02d}" if m else None
    return code


def _extract_st_code(name):
    if _RANGO_CODIGOS_RE.search(name):
        # Ver Corrección 9 -- "ST-15 - ST-20"/"[ST-31]~[ST-36]" describen
        # un lote de mazos, no un ST único; extraer el primer extremo sería
        # un código inventado (TestRangoDeCodigos, test_classify_product.py).
        return None
    m = _ST_CODE_RE.search(name)
    if m:
        return f"ST{int(m.group(1)):02d}"
    m = _STARTER_DECK_NUM_RE.search(name.lower())
    if m:
        return f"ST{int(m.group(1)):02d}"
    return _lookup_code(_normalize_for_lookup(name), _ST_TITLE_CODES)


def _extract_prb_code(name):
    if _RANGO_CODIGOS_RE.search(name):
        return None
    m = _PRB_CODE_RE.search(name)
    if m:
        return f"PRB{int(m.group(1)):02d}"
    return _lookup_code(_normalize_for_lookup(name), _PRB_TITLE_CODES)


def _extract_op_code(name):
    if _RANGO_CODIGOS_RE.search(name):
        return None
    m = _OP_CODE_RE.search(name)
    if m:
        return f"OP{int(m.group(1)):02d}"
    return _lookup_code(_normalize_for_lookup(name), _OP_TITLE_CODES)


def _extract_eb_code(name):
    if _RANGO_CODIGOS_RE.search(name):
        return None
    m = _EB_CODE_RE.search(name)
    if m:
        return f"EB{int(m.group(1)):02d}"
    return _lookup_code(_normalize_for_lookup(name), _EB_TITLE_CODES)
```

**Corrección 9 (NUEVA, mismo origen que la 8) -- guarda de rango de código
portada de `_RANGO_CODIGOS_RE`/`_SET_CODE_PREFIXES` (ya definidas y
validadas en el `classify.py` actual, reutilizadas tal cual, sin cambios).**
El primer borrador de esta guía no las mencionaba en ningún sitio de §7, así
que las seis funciones de extracción cogían el primer extremo de un rango
(`"Sobre ST-15 - ST-20 Release Event Pack"` → `ST15`) como si fuera el
código propio del producto -- exactamente el bug que `_RANGO_CODIGOS_RE`
existe para evitar (`TestRangoDeCodigos`, 3 casos reales). La guarda va
DENTRO de cada función de extracción, no en la condición de activación de
família en `_classify_pass` (§8) -- un rango sigue siendo, por ejemplo, un
Starter Deck real (la família se activa igual por el código/keyword
presente), solo que sin un código propio único que asignarle.
`_extract_dp_code_and_main_set()` (§4.2) necesita la misma guarda al
principio; con `dp_code=None`, `main_set` sale `None` automáticamente vía
`_DP_TO_OP.get(dp_code)`.

`_extract_dp_code_and_main_set()` está en §4.2 (actualizada con la guarda de
rango de la Corrección 9). `_extract_generic_code()` (código de
acompañamiento para `LOTE_CARTAS`/`PROMO_CARD`, reutilizando
`_SET_CODE_PREFIXES`, SIN el lookahead de la Corrección 8 -- ver nota
arriba) está en §2, Corrección 6. `_STARTER_DECK_NUM_RE` (número suelto tras
"Starter Deck", sin ninguna letra "ST") está en §4.4, Corrección 7.

---

## 8. `classify_product()` completo

**Estructura en dos funciones (nueva en esta ronda, ver §4.5):**
`_classify_pass()` hace todo el trabajo de Fase 0 + Fase 1 + Fase 2 sobre un
`text` ya construido; `classify_product()` (el punto de entrada público)
decide si hace falta una segunda pasada con tags.

```python
def _classify_pass(name, variant_title, tags) -> Classification:
    text = f"{name} {variant_title or ''} {tags or ''}".lower()

    # FASE 0
    if any(kw in text for kw in ("cgc", "psa", "bgs", "lote")):
        return Classification("LOTE_CARTAS", _extract_generic_code(name), _detect_language(name) or "EN", None)

    if any(kw in text for kw in ("promo card", "carta promo", "carta promocional",
                                  "promo pack", "promotion pack")):
        return Classification("PROMO_CARD", _extract_generic_code(name), _detect_language(name) or "EN", None)
    if re.search(r"\bP-?\d{2,4}\b", name, re.IGNORECASE):
        return Classification("PROMO_CARD", _extract_generic_code(name), _detect_language(name) or "EN", None)

    if _DON_CARD_RE.search(text) and not _SEALED_PRODUCT_CONTEXT_RE.search(text):
        dp_match = _DOUBLE_PACK_SET_CODE_RE.search(text)
        code = f"DP{int(dp_match.group(1)):02d}" if dp_match else None
        return Classification("PROMO_CARD", code, _detect_language(name) or "EN", None)

    if any(kw in text for kw in ("mystery pack", "mystery box")):
        return Classification("MYSTERY_PACK", None, _detect_language(name) or "EN", None)

    if "dice" in text:
        return Classification("DICE_ACCESSORY", None, _detect_language(name) or "EN", None)

    # FASE 1
    language = _detect_language(name) or _detect_language(variant_title) or (
        "EN" if "non-english" not in text and "non english" not in text else None
    )

    # FASE 2
    if _DF_CODE_RE.search(text) or any(kw in text for kw in
                                        ("devil fruits collection", "fruta del diablo")):
        return Classification("DEVIL_FRUITS_COLLECTION", _extract_df_code(name), language, None)

    if _IB_CODE_RE.search(text) or "illustration box" in text:
        return Classification("ILLUSTRATION_BOX", _extract_illustration_box_code(name), language, None)

    if "playmat" in text or "tapete" in text:
        code = _extract_volume_code(name, "VOL")
        if code is None:
            code = _lookup_code(_normalize_for_lookup(name), _PLAYMAT_CHARACTER_CODES)
        return Classification("PLAYMAT", code, language, None)

    if "sleeve" in text or "funda" in text:
        return Classification("SLEEVES", _extract_volume_code(name, "VOL"), language, None)

    if "premium card collection" in text:
        code = _extract_volume_code(name, "VOL")
        if code is None:
            code = _lookup_code(_normalize_for_lookup(name), _PREMIUM_CARD_COLLECTION_EDITION_CODES)
        return Classification("PREMIUM_CARD_COLLECTION", code, language, None)

    if any(kw in text for kw in ("learn together", "learn to play", "aprende a jugar")):
        return Classification("STARTER_DECK", None, language, None, packaging="sobre")

    if _ST_CODE_RE.search(text) or "starter deck" in text or "ultra deck" in text or "mazo" in text:
        pkg = _detect_packaging("STARTER_DECK", text)
        code = _extract_st_code(name)  # ST-NN, o número suelto (Corrección 7), o tabla de títulos
        if code is None:
            code = _lookup_code(_normalize_for_lookup(name), _STARTER_DECK_CHARACTER_CODES)
        return Classification("STARTER_DECK", code, language, None, packaging=pkg)

    if _DOUBLE_PACK_SET_CODE_RE.search(text) or "double pack" in text or "doble pack" in text:
        pkg = _detect_packaging("DOUBLE_PACK", text)
        dp_code, main_set = _extract_dp_code_and_main_set(name, text)
        return Classification("DOUBLE_PACK", dp_code, language, main_set, packaging=pkg)

    # A partir de aquí, cada família solo puede activarse por su PROPIO
    # código explícito o su PROPIA tabla de títulos -- nunca por la de
    # otra família (ver Corrección 4, §4).
    if _PRB_CODE_RE.search(text) or _lookup_code(_normalize_for_lookup(name), _PRB_TITLE_CODES):
        pkg = _detect_packaging("PREMIUM_BOOSTER_BOX", text)
        return Classification("PREMIUM_BOOSTER_BOX", _extract_prb_code(name), language, None, packaging=pkg)

    if _OP_CODE_RE.search(text) or _lookup_code(_normalize_for_lookup(name), _OP_TITLE_CODES):
        pkg = _detect_packaging("ONE_PIECE", text)
        main_set = _extract_op_code(name)
        return Classification("ONE_PIECE", main_set, language, main_set, packaging=pkg)

    if (_EB_CODE_RE.search(text) or "extra booster" in text
            or _lookup_code(_normalize_for_lookup(name), _EB_TITLE_CODES)):
        pkg = _detect_packaging("EXTRA_BOOSTER", text)
        return Classification("EXTRA_BOOSTER", _extract_eb_code(name), language, None, packaging=pkg)

    if _GENERIC_SEALED_CATCHALL_RE.search(text):
        # Corrección 10 (NUEVA -- decisión de producto 2026-08-30, cierra
        # §11.4, ya no bloqueante): sin código ni título reconocible, pero
        # con keyword de producto sellado ("booster"/"caja"/"box"/"sobre(s)")
        # -- mismo comportamiento que el sistema actual (BOOSTER_BOX/
        # BOOSTER_PACK genéricos, CLASSIFICATION_RULES: "booster box"/"caja"/
        # "booster"/"sobre "): se asume ONE_PIECE, el booster principal, el
        # que de verdad vende cualquier tienda sin especificar más (8 casos
        # reales documentados: "Booster Box EN", "Caja 24 Sobres Inglés",
        # "One Piece Booster Box Castellano", "Sobre Inglés"...). set_code/
        # main_set quedan None -- no hay ninguna señal de QUÉ lanzamiento es,
        # solo de que es un booster sellado de algún tipo. No se pierde
        # cobertura respecto a hoy; las tags (§4.5) siguen aportando valor
        # para resolver este caso cuando name+variant solos no traen ni
        # siquiera esta keyword genérica.
        pkg = _detect_packaging("ONE_PIECE", text)
        return Classification("ONE_PIECE", None, language, None, packaging=pkg)

    # Código de acompañamiento también para el catch-all genérico
    # (Corrección 8, §7) -- mismo principio que LOTE_CARTAS/PROMO_CARD
    # (Corrección 6, §2), generalizado un paso más: si ninguna família de
    # Fase 2 reclama el texto como su propio producto sellado (por ejemplo,
    # por llevar el lookahead (?!-\d) de la Corrección 8), el código sigue
    # siendo información barata de conservar aunque nunca se use para
    # comparar precio (OTROS es not_applicable en matcher.py, este dato es
    # solo de referencia). _extract_generic_code() usa _GENERIC_CODE_RE, SIN
    # el lookahead de família -- necesita seguir siendo permisivo para
    # capturar el código de acompañamiento igual que ya hace en Fase 0.
    return Classification("OTROS", _extract_generic_code(name), language, None)


def classify_product(name, variant_title=None, extra_type_hint=None) -> Classification:
    """Punto de entrada público. name+variant primero, sin tags; las tags
    (extra_type_hint) solo entran si eso da OTROS -- ver §4.5."""
    if not name:
        return Classification("OTROS", None, None, None)

    sin_tags = _classify_pass(name, variant_title, tags=None)
    if sin_tags.product_type != "OTROS" or not extra_type_hint:
        return sin_tags

    return _classify_pass(name, variant_title, tags=extra_type_hint)
```

**Nota:** el catch-all genérico ("booster/caja/box/sobre sin código ni
título → `ONE_PIECE`") ya está decidido — Corrección 10, §4 paso 11, cierra
§11.4 (decisión de producto 2026-08-30: mantenerlo, mismo comportamiento que
el sistema actual). Ver también §4.5 para su relación con las tags.

---

## 9. Fase 3 — Identity Resolution

Sin cambios respecto a la versión anterior de esta guía — no se ha tocado en
esta ronda de validación (necesita BBDD real, fuera del alcance de un
overview solo de Fases 0-2).

```
a. STARTER_DECK: ST-NN + match contra nombre oficial del producto
                 (incluye Learn Together como caso de variante única)
b. PREMIUM_CARD_COLLECTION: nombre de línea + match contra nombre oficial
c. NUEVO -- para cualquier família sin variante resuelta todavía:
     1. intento de match EXACTO contra name_canonical (lower(), sin tocar similarity())
     2. si falla, fallback a similarity() / pg_trgm (comportamiento actual)
     3. si tampoco resuelve: bucket genérico -> needs_review, NUNCA unmatched silencioso
```

### 9.0 Definición de `quantity_ambiguous`

Sin cambios respecto a la versión anterior — reutiliza `_PACKAGING_UNITS` de
§6, no se vio afectada por los bugs de esta ronda.

### 9.1 Evidence Builder / Decision Policy

Sin cambios respecto a la versión anterior de esta guía.

```python
@dataclass
class MatchEvidence:
    exact_name_match: bool
    set_code_match: Optional[bool]
    language_match: bool
    packaging_match: bool
    similarity_score: Optional[float]
    is_fallback_candidate: bool
    quantity_ambiguous: bool


def decide(evidence: MatchEvidence) -> str:
    if evidence.exact_name_match:
        return "confirmed"
    if evidence.set_code_match is False:
        return "unmatched"
    if (not evidence.is_fallback_candidate and evidence.set_code_match
            and evidence.language_match and evidence.packaging_match
            and not evidence.quantity_ambiguous):
        return "confirmed"
    if evidence.set_code_match is None:
        if evidence.similarity_score is None:
            return "unmatched"
        if evidence.similarity_score > 0.6 and evidence.language_match:
            return "confirmed"
        if evidence.similarity_score < 0.35:
            return "unmatched"
        return "needs_review"
    return "needs_review"
```

---

## 10. Decisiones cerradas (resueltas en esta y en la ronda anterior)

- **`PROMO_CARD`, `MYSTERY_PACK`, `DICE_ACCESSORY`** suben a Fase 0 junto a
  `LOTE_CARTAS` → `not_applicable` directo. Ver §2.
- **El código de "Don!!" SÍ se conserva** (informativo, no se usa para
  comparar) — cambio de esta ronda, ver §2.
- **`quantity_ambiguous`** reutiliza `_PACKAGING_UNITS`. Ver §9.0.
- **BBDD nueva** — no hace falta migración de `PREMIUM_COLLECTION` →
  `PREMIUM_CARD_COLLECTION`/`PREMIUM_BOOSTER_BOX`.
- **Tablas de título por línea, nunca compartidas** (NUEVO, esta ronda) —
  `_OP_TITLE_CODES`/`_PRB_TITLE_CODES`/`_EB_TITLE_CODES` sustituyen a la
  antigua `_RELEASE_TITLE_CODES` única. Ver §4 Corrección 4 y §7.
- **`main_set` de Double Pack se deriva de `_DP_TO_OP`** (NUEVO). Ver §4.2.
- **Premium Card Collection tiene tabla propia de líneas**, nunca la de
  OP/PRB/EB (NUEVO). Ver §4.3.
- **Starter Deck tiene tabla de títulos completos** además de la de
  personajes (NUEVO). Ver §4.4.
- **Starter Deck sin mapeo a `main_set`** — investigado y descartado, no hay
  relación 1:1 con OP en los datos reales (NUEVO). Ver §4.4 y §11.
- **Playmat reconoce "tapete"**, Sleeves reconoce singular (NUEVO). Ver §4.
- **`LOTE_CARTAS`/`PROMO_CARD` conservan un código de acompañamiento**
  (NUEVO, ronda CSV) — mismo principio que "Don!!", generalizado. Ver §2
  Corrección 6.
- **Starter Deck reconoce el número suelto sin "ST"** (NUEVO, ronda CSV).
  Ver §4.4 Corrección 7.
- **Las seis regex de código de família de Fase 2 excluyen numeración de
  carta individual** (`(?!-\d)`, NUEVA -- encontrada al contrastar contra la
  suite real, no solo contra nombres sueltos) -- `_GENERIC_CODE_RE` de Fase 0
  se queda deliberadamente sin este lookahead. Ver §7 Corrección 8.
- **Las funciones de extracción de código de Fase 2 llevan guarda de rango**
  (`_RANGO_CODIGOS_RE`, portada del `classify.py` actual sin cambios, NUEVA)
  -- un rango de códigos no tiene código propio que inventar. Ver §7
  Corrección 9.
- **Catch-all genérico a `ONE_PIECE` -- decidido (NUEVO, cierra §11.4)**:
  sin código ni título reconocible pero con keyword de sellado ("booster"/
  "caja"/"box"/"sobre(s)"), se asume `ONE_PIECE` con `set_code`/`main_set`
  en `None` -- mismo comportamiento que el sistema actual. Decisión de
  producto 2026-08-30. Ver §4 paso 11, §7 Corrección 10, §4.5.
- **Tags del comercio (`extra_type_hint`) como respaldo de dos pasadas**
  (NUEVO, ronda CSV) — hueco de diseño completo que no existía en ninguna
  versión anterior de esta guía. Ver §4.5.

## 10.1 Validación empírica

### Primera ronda (razonamiento sobre 286 nombres, sin ejecutar código)

Se comparó el diseño, caso por caso, contra 194 nombres del catálogo oficial
de Bandai + 92 strings de producción reales extraídos de
`test_classify_product.py`. Resultado antes de corregir: 30 regresiones
reales de 286 (10.5%), 36 mejoras concretas. Causas raíz encontradas y
corregidas en esa ronda: `LOTE_CARTAS` sin "lote" genérico, excepción "Don!!"
no portada, `ONE_PIECE` sin fallback de título (18 de las 30 regresiones),
catch-all genérico pendiente de decidir, `PREMIUM_CARD_COLLECTION` sin
fallback a Vol.N.

### Segunda ronda (esta revisión) — prototipo ejecutado de verdad contra 265 nombres reales

A diferencia de la primera ronda, aquí se construyó un prototipo funcional
(`classify_product_v2`) y se corrió literalmente contra los 194 nombres del
catálogo oficial + 72 nombres reales de `test_classify_product.py` (265
únicos), comparando resultado contra `classify_product()` actual caso por
caso. Esto encontró problemas que el razonamiento teórico de la primera
ronda no había detectado:

| # | Problema encontrado | Alcance real | Corregido en |
|---|---|---|---|
| 1 | Tabla de títulos compartida entre OP/PRB/EB — invalidaba la Corrección 3 | 17 boosters del catálogo + colisión de código real (`Eb-02`→`EB01`) | §4 Corrección 4, §7 |
| 2 | Playmat no reconocía "tapete" | Variante real en español | §4 |
| 3 | Sleeves solo comprobaba plural, el catálogo usa singular | 21 de 194 productos reales | §4 |
| 4 | Devil Fruits Collection sin fallback Vol.N→DFNN | 3 casos reales, regresión respecto al sistema actual | §7 |
| 5 | Illustration Box sin fallback IB-NN→VOLNN | 2 casos reales (56 store_product según historial de classify.py) | §7 |
| 6 | Starter Deck: regex sin separador de espacio (`"ST 36"`) | Regresión respecto al sistema actual (bug Arte9 reintroducido) | §4.4, §7 |
| 7 | Double Pack sin fallback de número escrito (`"Double Pack Set Vol.N"`) | Los 12 Double Pack del catálogo oficial (sin `DP-NN` en el nombre) | §4.2 |
| 8 | Double Pack sin `main_set` derivado | Todos los Double Pack (dato disponible, sin usar) | §4.2 |
| 9 | Premium Card Collection reutilizaba la tabla de OP/PRB/EB para su fallback de código | Riesgo de colisión de família (caso documentado: "The Best Vol.2"→`PRB02` en vez de tratarse como família aparte) | §4.3 |
| 10 | Starter Deck sin tabla de títulos completos (solo personajes) | 8 casos reales del catálogo con color+personaje, antes ambiguos a propósito | §4.4 |

**Resultado final tras aplicar las 10 correcciones**, contra los mismos 265
nombres: **0 regresiones reales** (la única pérdida de dato, el código del
caso "Don!!", quedó corregida también, ver §2), 20 códigos recuperados que
antes eran `None`, 22 tipos recuperados que antes eran `OTROS`, 99 idénticos
al sistema actual, 67 reclasificaciones correctas propias del rediseño (PRB/
EB separados de OP, BOOSTER_CASE absorbido en `packaging`, Learn Deck
fusionado en Starter Deck), y 36 nombres que siguen sin identificar en ambos
sistemas — **por diseño, no por bug** (ver §11.6, siempre fue así).

### Tercera ronda — validación contra 2163 filas reales de producción (47 tiendas)

El catálogo oficial + tests reales son datos limpios y curados; para
comprobar el comportamiento contra la variedad real de cómo 47 tiendas
distintas nombran sus productos, se corrió el mismo prototipo contra
`multi_tienda_one_piece.csv` (2163 filas, columnas `name`+`variant` reales de
producción). Antes de comparar, se detectó y se dejó aparte un problema
metodológico: recalcular `classify_product()` actual con solo `name`+
`variant` no reproduce 86 filas del CSV, porque esas 86 se resolvieron en
producción vía `raw_tags` — señal que este CSV no exporta como columna. Esto
llevó directamente al hallazgo de §4.5 (tags no contempladas en el diseño
original). La comparación "esquema actual vs nuevo" se hizo en las mismas
condiciones en ambos lados (`name`+`variant`, sin tags) para no mezclar ese
hallazgo con regresiones reales del algoritmo.

Primera pasada contra las 2163 filas: **82 regresiones**. Investigadas y
corregidas, las 3 causas fueron:

| # | Problema encontrado | Alcance real | Corregido en |
|---|---|---|---|
| 11 | `LOTE_CARTAS` no conservaba el set de origen de la carta gradeada | 69 filas reales (CGC/PSA, muchas variantes de grado por carta) | §2 Corrección 6 |
| 12 | `PROMO_CARD` sellada con contexto de set tampoco lo conservaba | 1 fila real | §2 Corrección 6 |
| 13 | Starter Deck sin fallback de número suelto sin "ST" (`_STARTER_DECK_NUM_RE`) | 12 filas reales, las 12 de la misma tienda (Gameria) | §4.4 Corrección 7 |

**Resultado final tras las 13 correcciones acumuladas** (10 de la segunda
ronda + 3 de esta), contra las 2163 filas reales: **0 regresiones**, 828
idénticas al sistema actual, 316 tipos recuperados (182 de ellas solo por el
fix de singular/plural de Sleeves — mucho más impacto real del que sugería
el catálogo oficial más pequeño), 3 códigos recuperados, 752 reclasificadas
correctamente por el rediseño, 264 sin identificar en ambos sistemas (mismo
patrón que §11.6). Además, se implementó y validó el soporte de tags (§4.5)
contra los 8 tests de código real de `TestExtraTypeHintDeTags` — los 8 dan el
resultado correcto.

**Conclusión combinada (265 + 2163 = 2428 casos reales verificados):** el
pipeline nuevo iguala o mejora al sistema actual en el 100% de los casos
conocidos — no hay ningún caso real verificado en el que empeore.

### Cuarta ronda — contraste contra la suite real `test_classify_product.py` (no solo nombres sueltos)

Las tres rondas anteriores compararon el prototipo contra **nombres extraídos
a mano** de la suite (72 de los 265, más los que aparecen tal cual en el CSV
de producción) -- no contra los **casos límite que la suite fija como
comportamiento exigido** (`TestRangoDeCodigos`, y el caso de carta individual
con numeración propia dentro de `TestExtraBooster`). Esto dejó pasar 2
regresiones reales que las rondas anteriores no detectaron por construcción,
no por mala suerte -- un nombre suelto sacado de un test no reproduce la
intención de ESE test si el propio test explota un caso límite deliberado:

| # | Problema encontrado | Alcance real | Corregido en |
|---|---|---|---|
| 14 | Códigos de família de Fase 2 sin `(?!-\d)` -- una carta individual con numeración propia (`"...EB02-003"`) auto-activaba la família como si fuera el producto sellado | `codigo-eb-de-CARTA-INDIVIDUAL-eb02-003-no-dispara-el-fallback-de-booster`, `test_codigo_eb_de_carta_individual_no_se_confunde_con_rango` | §7 Corrección 8 |
| 15 | Funciones de extracción de Fase 2 sin guarda de rango -- `"ST-15 - ST-20"`/`"[ST-31]~[ST-36]"` extraían el primer extremo como código propio | `TestRangoDeCodigos` (3 casos) | §7 Corrección 9 |

**Pendiente de re-ejecutar contra el prototipo real** tras aplicar las
Correcciones 8 y 9 (no hecho todavía en esta revisión del documento -- ver
§12, paso 8 actualizado). Lección metodológica para futuras rondas: antes de
cerrar un pipeline como "0 regresiones", correr literalmente la suite de
tests (no una selección de nombres derivada de ella) es la única forma de no
perder los casos límite que la propia suite existe para fijar.

---

## 11. Huecos que siguen abiertos

### 11.4 Catch-all genérico sin código ni título — RESUELTO (decisión de producto 2026-08-30)

**Cerrado.** 8 casos reales de producción (`"Booster Box EN"`, `"Caja 24
Sobres Inglés"`, `"One Piece Booster Box Castellano"`, `"Sobre Inglés"`) no
tienen ni `OP-NN` ni título reconocible — hoy se capturan solo por keyword
genérica. Decisión: mantener el catch-all de última instancia a `ONE_PIECE`
(mismo comportamiento que el sistema actual) en vez de aceptar la pérdida de
cobertura — ver Corrección 10, §4 paso 11, §7. No se pierde ningún caso real
conocido respecto a hoy.

Las tags (§4.5) siguen aportando valor donde ni siquiera esta keyword
genérica de sellado aparece en `name`+`variant`.

### 11.5 Sub-línea de Sleeves

Riesgo aceptado (decisión ya tomada: opción simplificada sin distinguir línea
TCG+/Limited/Premium Matte) — sin cambios en esta ronda. El fix de
singular/plural (§4) solo afecta a si se reconoce el TIPO, no a esta
ambigüedad de sub-línea dentro del tipo, que sigue abierta y aceptada tal
cual.

### 11.6 Accesorios genéricos sin categoría de comparación

Sin cambios — comportamiento correcto, intencional. Confirmado en la segunda
ronda: de 265 nombres, 36 quedan sin identificar en ambos sistemas, y los 36
son exactamente esto — binders, card cases, storage boxes, tin packs, sets de
aniversario/especiales, merchandising cruzado (Funko Pop) — ningún canónico
de producto sellado razonable con el que compararlos. Sin acción pendiente.

### 11.7 Nombre de la tabla `_PACKAGING_UNITS` para `DICE_ACCESSORY`/`MYSTERY_PACK` tras el cambio a Fase 0

Sin cambios respecto a la ronda anterior — siguen sin necesitar entrada en
`_PACKAGING_UNITS` ni en `PRODUCT_TYPE_TO_CATEGORY_SLUG`.

### 11.8 Ambigüedad Vol.2 en Live Action Edition (NUEVO)

`"Premium Card Collection -Live Action Edition vol.2 Baroque Works-"` y
`"...vol.2 Straw Hat Crew-"` son dos productos reales distintos que
comparten el mismo `Vol.2` — el identificador Vol.N por sí solo no los
distingue entre sí. Encontrado al construir la tabla de §4.3, no resuelto en
esta ronda por estar fuera del alcance del cambio que lo hizo visible.
Necesitaría, si se quiere resolver, un lookup adicional por el nombre del
personaje/tripulación dentro de esta sub-línea concreta (mismo patrón que
`_PLAYMAT_CHARACTER_CODES`).

### 11.9 Mapeo Starter Deck → main_set (NUEVO, investigado y descartado por ahora)

Se investigó aplicar a Starter Deck el mismo tratamiento que a Double Pack
(§4.2). Las fechas de lanzamiento reales del catálogo oficial no muestran una
relación 1:1 con OP (los Starter Deck se lanzan en tandas de hasta 6 el mismo
día, sin booster OP asociado esa fecha). Queda pendiente por si existe otra
fuente de agrupación oficial (Bandai agrupa Starter Decks por catálogo en
<https://en.onepiece-cardgame.com/products/?subcategory=decks&page=1&view=normal>)
que sí permita construir esta relación — **decisión aplazada, revisar más
adelante.**

---

## 12. Próximos pasos

1. **Resuelto — §11.4** (catch-all genérico sin código ni título): decisión
   de producto 2026-08-30, mantener catch-all a `ONE_PIECE` (Corrección 10).
   Ya no bloquea la implementación.
2. Confirmar §11.7 (`DICE_ACCESSORY`/`MYSTERY_PACK` sin entrada en
   `PRODUCT_TYPE_TO_CATEGORY_SLUG`).
3. Decidir si se resuelve §11.8 (ambigüedad Vol.2 de Live Action Edition) o
   se acepta como riesgo, igual que §11.5.
4. Implementar en `shared/classify.py` real: `Classification.packaging`,
   las tablas separadas por línea (§7, §4.3, §4.4), `_DP_TO_OP` y
   `_extract_dp_code_and_main_set()` (§4.2), `_extract_generic_code()` (§2),
   el soporte de tags de dos pasadas (§4.5), el catch-all de última
   instancia a `ONE_PIECE` (§4 paso 11, Corrección 10), y `classify_product()`
   reescrito con las 15 correcciones acumuladas de §10.1 aplicadas.
5. Implementar `_quantity_ambiguous()`, `MatchEvidence`/`build_evidence()`/
   `decide()` en `matcher.py` (sin cambios respecto al diseño original, §9.1).
6. Sembrar la BBDD nueva con las categorías/slugs correspondientes,
   incluyendo los identificadores inventados de Premium Card Collection
   (§4.3) y Starter Deck por título (§4.4) — igual que ya se hace hoy con
   Playmat.
7. **Ya hecho:** re-ejecutar la comparación empírica contra los 265 nombres
   reales (catálogo+tests) — 0 regresiones. **Ya hecho también:**
   re-ejecutar contra 2163 filas reales de producción (47 tiendas) — 0
   regresiones tras las 3 correcciones adicionales que solo esa escala de
   datos reveló (§10.1, tercera ronda).
8. Correr **toda** la suite (`test_classify_product.py`, `test_matcher.py`,
   `test_matcher_casos_reales_cola.py`, `test_parse_price.py`,
   `TestExtraTypeHintDeTags`) contra el pipeline nuevo real (no el prototipo)
   y comparar resultado por caso documentado, no solo el conteo de
   pass/fail. **Parcialmente hecho ya, en el diseño:** el contraste contra
   `TestRangoDeCodigos` y el caso de carta individual (`EB02-003`) de la
   suite real encontró las Correcciones 8 y 9 (§7, §10.1 cuarta ronda) antes
   de tocar `shared/classify.py` -- sigue pendiente re-ejecutar el prototipo
   completo con esas dos correcciones aplicadas y confirmar que el resto de
   la suite (no solo esos dos puntos) sigue en verde.
9. Revisar más adelante §11.9 (mapeo Starter Deck → main_set) contra la
   página de catálogo de Bandai por si permite construir la relación que los
   datos de fecha de lanzamiento no permitieron.

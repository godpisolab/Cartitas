# Pendientes — Motor de matching (consolidado)

Solo lo que queda por hacer o decidir. El razonamiento completo de cada punto vive en los documentos de investigación (`investigacion-motor-matching-parte1/2/3.md`, `implementacion-auto-confirmado-setcode.md`) — aquí solo la lista accionable.

---

## Bugs a corregir

### 1. `is_prerelease_variant()` — sin implementar todavía
Falso positivo real confirmado en producción de prueba: `"Mazo Iniciación Los Mugiwaras ST-01"` (sin mención de pre-release) confirmó contra `"(Pre-release) ST-01 PRE"` con score `0.08`. Especificación completa, con test de regresión y su control positivo, ya escrita en `implementacion-auto-confirmado-setcode.md` sección 4 — falta implementarla.

### 2. `BOOSTER_CASE` — la palabra clave nueva es demasiado amplia, colisiona con "Card Case"
Encontrado al verificar el commit `161bae5`: la categoría `booster-case` tiene sembrado un único producto, y es incorrecto — `"Limited Card Case -Monkey.D.Luffy- EN"` (un estuche/funda de cartas, producto ya existente y correcto en sí mismo) coló como `BOOSTER_CASE` porque el patrón `"case -"` es demasiado genérico. Efecto práctico: `booster-case` está, a día de hoy, **vacía de contenido real** — cualquier "Case de sobres" real que llegue de una tienda caerá en el mismo problema de categoría vacía que se intentaba resolver (fallback cross-categoría). Arreglo: acotar el patrón (`"booster case"`, `"box case"`, `"(case)"` seguido de contexto de sobres/cajas, no `"case -"` a secas) y quitar el producto mal clasificado.

**Hecho (2026-08-28):** `BOOSTER_CASE` salió de `CLASSIFICATION_RULES` (un keyword suelto no bastaba) y pasó a `_BOOSTER_CASE_RE`/`_BOOSTER_CASE_CONTEXT_RE` en `shared/classify.py` — "case" a secas + contexto (código de set reconocible o palabra de caja/booster/sellado) en el mismo texto. Validado fila a fila contra las 34 menciones reales de "case" en `multi_tienda_one_piece.csv`: cubre tanto los Case con "booster"/"box" pegado como los que no lo llevan (`"OP-19 Case"`, `"Case sellado OP-16"`, `"Case OP02 Paramount War (12 cajas)"`), y excluye los 7 accesorios reales (`Dice Case`/`Card Case`/`Playmat...Case`, más el Funko Pop) sin necesitar mirar en qué categoría cayeron. Tests de regresión en `test_classify_product.py`.

**Pendiente, fuera del alcance de un cambio de código:** el producto `"Limited Card Case -Monkey.D.Luffy- EN"` ya sembrado con la categoría vieja (equivocada) en tu BBDD real sigue ahí — `seed_official_catalog.py` es idempotente por `name_canonical` (`_insert_if_new`), así que re-ejecutarlo NO corrige una fila ya existente, solo evita crear nuevas mal clasificadas. Hace falta corregirlo a mano:

```sql
DELETE FROM product WHERE name_canonical = 'Limited Card Case -Monkey.D.Luffy- EN';
```

(o reasignar su `category_id` en vez de borrarlo, si prefieres conservarlo bajo otra categoría de accesorios) y volver a ejecutar `seed_official_catalog.py` para que se regenere si corresponde.

### 3. `booster-case` necesita productos canónicos reales sembrados
Una vez arreglado el punto 2, la categoría sigue sin ningún Case real. Pendiente: generar los canónicos (probablemente derivados de cada Booster Box existente) — **el multiplicador de cajas por Case no es uniforme**, verificado en el propio CSV: `PRB-02` es `x10`, los `OP-NN` vistos son `x12`. No asumir un único número para todas las líneas de producto.

**Hecho (2026-08-28):** `seed_official_catalog.py` genera ahora un Case (EN+JP) por cada release booster con multiplicador conocido — `_CASE_MULTIPLIER_BY_CATEGORY = {"booster-box": 12, "premium-collection": 10}`, derivado de `box_category_slug` (la categoría real que ya resuelve `classify_product()` para la variante caja), no del prefijo del código. `starter-deck`/`double-pack` se dejan fuera A PROPÓSITO — cero menciones reales de "Case" para esas líneas en las 34 filas revisadas, no se inventa un multiplicador sin dato. Verificado con una siembra real completa contra `cartitas_test`: 46 productos `booster-case` (23 releases × EN/JP), todos con `set_code`/`language` correctos y ninguno colisionando con `"Limited Card Case -Monkey.D.Luffy-"` (confirmado ausente de los sembrados, correctamente omitido como `OTROS`).

### 4. `"Don!! (DP10 Map) - One Piece Products (DON!!)"` — falso positivo confirmado, no solo duda
Tu valoración: tiene pinta de ser una **carta suelta promocional DON!!** (la que viene de regalo dentro del Double Pack Set Vol.10, tematizada como mapa), no el Double Pack sellado completo — vendida por separado como coleccionable individual. Si es así, hoy confirma incorrectamente contra `"Double Pack Set Vol.10 DP-10 EN"`, un producto sellado con una unidad de venta y un precio completamente distintos.

Mismo patrón de fondo que la carta promo `Ichiban Kuji`/`OP13` del arranque de esta investigación (una carta individual coincide de código con un producto sellado que la contiene o la acompaña) — pero ahí el problema era el *fallback cross-categoría* sobre una categoría vacía (`promo-card`); aquí es distinto: `classify_product()` clasificó esto directamente como `DOUBLE_PACK` (por detectar "double pack"/`DP10` en el texto), cuando en realidad es una carta suelta, no el pack sellado.

Pendiente: revisar si `classify_product()` necesita una señal adicional para "esto es un insert/carta suelta de regalo, no el producto sellado que la acompaña" — patrones como "Don!! Card" mencionado junto a un código de set, sin palabras como "set"/"pack"/"caja" que confirmen que es el sellado completo. Similar en espíritu a la lista `LOTE_CARTAS` (`cgc`/`psa`/`bgs`) pero para un caso distinto (no cartas gradeadas, cartas promocionales de regalo).

**Hecho (2026-08-28):** guard nuevo en el fallback de código `DP-NN` (`_DON_CARD_RE`/`_SEALED_PRODUCT_CONTEXT_RE` en `shared/classify.py`) — "Don!!" + código, SIN ninguna de "set"/"pack"/"caja"/"box"/"sobre" en el mismo texto, clasifica `PROMO_CARD` en vez de `DOUBLE_PACK`. Se eligió `PROMO_CARD` (no `LOTE_CARTAS`) porque sí tiene sentido comparable contra un canónico si algún día se siembra uno (punto 8 sigue abierto). Validado contra las 4 menciones reales de "Don!!"+código DP en el CSV: solo la fila del bug cambia — las otras 3 (`"Special DON!! Card Pack DP-06..."`, `"...DP-05..."`, el propio `"Double Pack Set Vol.10 [DP-10]..."`) siguen `DOUBLE_PACK` sin tocar, porque sí traen "Pack"/"Set" en el texto.

---

## Casos a verificar manualmente (no son bugs de código, necesitan tu criterio)

### 5. ~~`"One Piece | DP-08 Legacy of the Master OP-12"` → confirmado contra `"Double Pack Set Vol.8 DP-08 EN"`~~ — verificado, correcto

Confirmado por ti: `DP-08` se lanzó junto a "Legacy of the Master" (OP-12) — mi dato del catálogo oficial estaba desactualizado/equivocado en esa asociación, no la tienda. El match es correcto, sin ninguna acción pendiente.

---

## A tener en cuenta — no bloqueante, sin decisión tomada todavía

### 6. Ampliar la siembra JP a todas las categorías de producto sellado con demanda real — decidido

Decisión: no limitar `_JP_VARIANT_CATEGORY_SLUGS` a `booster-box`/`booster-pack` — ampliar a **todo lo que sea producto sellado con importación japonesa real**: `booster-pack`, `booster-box`, `booster-case` (una vez sembrado, punto 3), `double-pack`, `premium-collection`. Motivado por la demanda ya demostrada en el CSV (16 filas `PRB-01`/`PRB-02` en japonés que hoy no pueden confirmar por falta de esa variante).

**Sin decidir todavía, no asumido aquí:** si el resto de categorías de `Sellado` (`starter-deck`, `illustration-box`, `devil-fruits-collection`, `learn-deck`) entran también en la ampliación o se quedan fuera — quedan fuera de la lista explícita de arriba a falta de confirmación, no por descarte. Playmat/dice-accessory/promo-card/mystery-pack se asumen fuera por naturaleza (accesorios o promocionales, no producto sellado que se importe en volumen).

Además, quedan **29 filas residuales** dentro de `booster-box`/`booster-pack` con idioma no coincidente **pese a que esas categorías ya tienen JP sembrado** — causa todavía sin investigar, podría no ser un problema de dato faltante sino un fallo puntual del scoring al elegir entre EN/JP cuando ambas variantes ya existen (ver duplicados de `set_code` en `implementacion-auto-confirmado-setcode.md`).

**Hecho (2026-08-28), la parte decidida:** `_JP_VARIANT_CATEGORY_SLUGS` en `seed_official_catalog.py` ampliada a `{"booster-box", "booster-pack", "booster-case", "double-pack", "premium-collection"}`. `starter-deck`/`illustration-box`/`devil-fruits-collection`/`learn-deck` siguen fuera, tal como queda "sin decidir" arriba -- no se tocaron. Las 29 filas residuales y la investigación del fallo de scoring EN/JP siguen sin investigar, fuera de esta ronda.

### 7. Ambigüedad de texto genuina (233→215 filas del análisis, ~14% del total no confirmado) — investigado, no es un problema de categorías

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

### 8. `mystery-pack` nunca tendrá producto canónico (decidido) — `promo-card` queda abierto

**`mystery-pack` — decisión permanente, no pendiente:** por su propia naturaleza (contenido aleatorio/sorpresa), no existe un "producto canónico" contra el que comparar — no tiene sentido sembrar nada aquí nunca. Se queda vacía a propósito, para siempre.

**`promo-card` — de momento no se dan de alta las cartas individuales, pero puede cambiar.** No es una decisión cerrada como `mystery-pack`: sigue abierta la puerta a sembrar canónicos de cartas promo concretas en el futuro (por ejemplo, a partir de lo que aparezca con volumen real en `missing-candidates`), solo que no se hace todavía. Bajo volumen actual, no urgente.

### 9. Bundle "Pack 5 Sobres" — decidido, mismo saco que `mystery-pack`, sin categoría propia
Se queda con la guarda de `cantidad_es_ambigua()` (siempre en `needs_review`) de forma permanente, no como solución provisional — mismo criterio que `mystery-pack` (punto 8): no se crea categoría propia para esto. No reconsiderar aunque aparezca más volumen en el scrape real, salvo que cambie explícitamente esta decisión.

### 10. Cantidades ambiguas sin resolver: `"x12"`/`"x6"`
Mismo tratamiento que el punto 9 — quedan en `needs_review` por precaución, sin saber si son de verdad una unidad distinta o solo una tienda describiendo mal una caja normal. No investigado más a fondo, bajo volumen (4 casos).

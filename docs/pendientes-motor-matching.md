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

**Decidido (2026-08-29):** `starter-deck` entra también en la ampliación — señal de demanda real confirmada en la auditoría de `needs_review` (Pokemillon vendía 7+ Starter Decks japoneses distintos, ST-08/ST-09/ST-11/ST-14/ST-33/ST-34/ST-36, sin ningún candidato JP posible). `illustration-box`/`devil-fruits-collection`/`learn-deck` siguen fuera de la lista explícita, a falta de confirmación, no por descarte. Playmat/dice-accessory/promo-card/mystery-pack se asumen fuera por naturaleza (accesorios o promocionales, no producto sellado que se importe en volumen).

Además, quedan **29 filas residuales** dentro de `booster-box`/`booster-pack` con idioma no coincidente **pese a que esas categorías ya tienen JP sembrado** — causa todavía sin investigar, podría no ser un problema de dato faltante sino un fallo puntual del scoring al elegir entre EN/JP cuando ambas variantes ya existen (ver duplicados de `set_code` en `implementacion-auto-confirmado-setcode.md`).

**Hecho (2026-08-28), la parte decidida:** `_JP_VARIANT_CATEGORY_SLUGS` en `seed_official_catalog.py` ampliada a `{"booster-box", "booster-pack", "booster-case", "double-pack", "premium-collection"}`. `starter-deck`/`illustration-box`/`devil-fruits-collection`/`learn-deck` siguen fuera, tal como queda "sin decidir" arriba -- no se tocaron. Las 29 filas residuales y la investigación del fallo de scoring EN/JP siguen sin investigar, fuera de esta ronda.

**Hecho (2026-08-29):** `starter-deck` añadida a `_JP_VARIANT_CATEGORY_SLUGS` (ver decisión más arriba) — 40 canónicos JP nuevos sembrados sobre `cartitas` real (uno por cada Starter Deck EN existente). `illustration-box`/`devil-fruits-collection`/`learn-deck` siguen sin decidir.

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

### 10. Cantidades ambiguas sin resolver: `"x12"`/`"x6"` — `x12` resuelto, `x6` sigue abierto

**Hecho (2026-08-29):** `"x12"` en un `BOOSTER_BOX` deja de ser solo sospechoso -- 12 es EXACTO el multiplicador real de Case para esa categoría (`_CASE_MULTIPLIER_BY_TYPE`, mismo dato que ya usaba `seed_official_catalog.py` para sembrar los canónicos Case). Verificado contra 3 casos reales (Master of Games, OP-14/OP-16/OP-17): los 3 clasifican ahora `BOOSTER_CASE` y auto-confirman contra el canónico ya sembrado, sin necesitar la palabra "case" en el texto. Sobre `name_variant_text`, no tags -- mismo criterio que el resto de señales caras de confirmar mal (Case cuesta ~12x una caja normal). Aplicado también a `PREMIUM_COLLECTION` (`x10`), sin caso real observado todavía que lo confirme.

**Sigue abierto:** `"x6"` en `Starter Deck EX Gear5 [ST21]` (Master of Games) -- `starter-deck` no tiene multiplicador de Case conocido (`_CASE_MULTIPLIER_BY_CATEGORY`/`_CASE_MULTIPLIER_BY_TYPE` no lo incluyen, cero evidencia real de "Starter Deck Case" todavía) y no existe ningún canónico Case de Starter Deck sembrado -- aunque se detectara, no habría contra qué confirmar. Queda en `needs_review` por precaución, igual que antes.

---

## 2026-08-29 — Auditoría completa de `needs_review` (182 → 49 filas)

Sesión de auditoría manual fila a fila contra un CSV exportado de Postgres real (`store_product` en `needs_review`), reproduciendo la lógica exacta de `_evaluate()`/`_best_candidate()` para explicar el motivo concreto de cada una. Ver también los puntos 6 y 10 de arriba (actualizados en esta misma ronda) y `tests/README.md` sección "Bugs reales encontrados" para los dos bugs de composición (regex sin `IGNORECASE`, `&` en las claves del lookup).

### 11. Lookups nuevos: personaje→código y título de release→código

Dos tablas whitelist nuevas en `shared/classify.py`, mismo espíritu que `_SET_CODE_PREFIXES` (lista blanca explícita, nunca "cualquier texto que suene a"):

- **`_STARTER_DECK_CHARACTER_CODES`** (31 personajes → ST-code): cubre tiendas que nombran el Starter Deck solo por el personaje protagonista, sin color ni código (inGenio BCN, Gameria). Los 4 personajes que Bandai reutilizó en Starter Decks de color distinto (Monkey D. Luffy ×4, Charlotte Katakuri ×2, Yamato ×2, Uta ×2) se dejan FUERA a propósito — sin la palabra de color no hay señal para desambiguar, mejor `needs_review` que un match falso silencioso. Antes de este fix, el candidato sugerido por similitud pura para varios de ellos era directamente incorrecto (ej. "Shanks"/"Buggy"/"Sabo" caían los tres en `ST05`, un Starter Deck genérico sin relación).
- **`_PLAYMAT_CHARACTER_CODES`** (5 personajes → pseudo-código inventado, ej. `SHANKS`/`NAMI`/`ACE`): a diferencia de Starter Deck, Bandai NUNCA asigna código real a los playmats con personaje — son identificadores inventados solo para poder desambiguar dentro de la categoría vía el mismo mecanismo `set_code`. Verificado contra los 18 playmats reales sembrados: ningún personaje se repite, así que ninguna entrada queda ambigua (a diferencia de Starter Deck).
- **`_RELEASE_TITLE_CODES`** (27 títulos oficiales de Bandai → código, ej. "Romance Dawn"→OP01, "The Best vol.2"→PRB02): cubre `BOOSTER_BOX`/`BOOSTER_PACK`/`PREMIUM_COLLECTION` nombrados por su título temático sin código (inGenio BCN). Bandai no repite título de release entre lanzamientos, así que no hay ambigüedad como sí la hay en Starter Deck.
- **`_normalize_for_lookup()`**: decodifica entidades HTML (`&#8217;`, `&amp;`, `&#038;` — vistas crudas, sin decodificar, en varios `raw_name` reales) antes de comparar contra las tablas de arriba.

Impacto medido: inGenio BCN pasó de 36 a 9 filas en `needs_review` (de las cuales 5 son personajes ambiguos dejados fuera a propósito).

### 12. `Vol.N -> VOLnn` para `PREMIUM_COLLECTION` — backfill de canónicos ya sembrados incluido

`PREMIUM_COLLECTION` no estaba en `_VOLUME_IDENTIFIED_PRODUCT_TYPES` (solo `ILLUSTRATION_BOX`/`PLAYMAT`) pese a que el catálogo oficial de Bandai NUNCA asigna código real a las ediciones "Premium Card Collection -X-" (`data/one_piece_tcg_products.json`: `"code": null` en las 17 variantes) — el "Vol.N" del propio nombre es la única señal real posible. Caso real: Mulligan vendía "Premium Card Collection Vol 3"/"Vol 4" sin ningún candidato con código al que compararse.

Como los canónicos YA sembrados también tenían `set_code=NULL` (nunca se les asignó al sembrarlos, mismo motivo), hizo falta un backfill puntual sobre `cartitas` real además del fix de extracción — 18 productos actualizados. **Colisión conocida y aceptada:** `VOL02` lo comparten 3 sub-líneas distintas ("Best Selection Vol.2", "Live Action Edition vol.2 Baroque Works", "Live Action Edition vol.2 Straw Hat Crew") — `_best_candidate` desempata por `similarity()` DESC como último criterio dentro del mismo `set_code`, así que un texto que sí mencione la sub-línea sigue resolviendo bien; uno genérico ("Premium Card Collection Vol 2" a secas) podría desambiguar mal. Sin caso real observado todavía, no se ha resuelto más a fondo.

### 13. Código explícito (`PRB-NN`/`DP-NN`) debe pisar el keyword genérico de tipo, no solo cuando `product_type` sigue en `OTROS`

Dos bugs gemelos, mismo patrón de fondo: `_EXTRA_BOOSTER_CODE_RE`/`_DOUBLE_PACK_CODE_RE`/etc. solo ascendían el `product_type` cuando este seguía en `OTROS` — si un keyword genérico (`"caja"`/`"sobre"`, sea del propio `name` o de una `raw_tags` reciclada, ver punto 7 de `tests/README.md`) ya lo había resuelto (mal) a `BOOSTER_BOX`/`BOOSTER_PACK` antes, el código explícito nunca tenía ocasión de corregirlo.

- **PRB-NN** (Saruman Games, `"Premium2 – PRB-02 sobre"`): "Premium2" no coincide con ningún keyword de `PREMIUM_COLLECTION`, así que "sobre" ganaba y el producto quedaba `BOOSTER_PACK` pese al código `PRB-02` explícito.
- **DP-NN** (Pokemillon, `"DP09 The Azure Sea's Seven OP14"`/`"DP-08 Legacy of the Master OP-12"`): `raw_tags` con "Sobre"/"Caja" (metadato de catálogo reciclado, no descripción real del producto) resolvían `BOOSTER_PACK`/`BOOSTER_BOX` antes de que `_DOUBLE_PACK_CODE_RE` tuviera ocasión de ascender a `DOUBLE_PACK`.

Arreglo: ambos códigos ahora pisan `BOOSTER_BOX`/`BOOSTER_PACK` ya resueltos, no solo `OTROS` — PRB es un prefijo reservado en exclusiva a `premium-collection`, DP a `double-pack` (`_SET_CODE_PREFIXES`), así que su presencia es señal más fuerte que un keyword genérico suelto. Impacto: 10 filas reales (3 directas + 7 de arrastre, otros DP-NN con el mismo patrón).

### 14. Categoría con un único SKU posible en todo el catálogo — auto-confirma sin depender de similitud

`LEARN_DECK`/`DICE_ACCESSORY` tienen exactamente 1 producto canónico sembrado cada una (`"Learn Together Deck Set EN"`, `"Official Dice and Dice Case Set EN"`) — ningún otro candidato posible existe en esas categorías, así que el umbral de similitud/`set_code` no aporta nada, solo bloqueaba un match que ya era inequívoco por construcción. `matcher._single_sku_categories()` (recalculado en cada `run_matching()`, no una lista fija a mano) identifica estas categorías y `_evaluate()` confirma directo cuando `es_fallback=False` y el idioma no contradice explícitamente al único candidato. Impacto: 12 filas (10 `LEARN_DECK` + 2 `DICE_ACCESSORY`).

### 15. `PrestaShopScraper` generalizado con el fix de nombres truncados de WooCommerce

Distrito Zero (PrestaShop, tema IQIT) truncaba el título en el listado exactamente igual que Arte9 (WooCommerce/Madara) — 19 de 20 `raw_name` en `needs_review` de esa tienda terminaban en `"..."` literal. El mecanismo (`_looks_truncated`/visitar la ficha individual para el `h1` completo) ya existía en `scrapers/woocommerce.py`, documentado como "por patrón, no por tienda", pero nunca se había enganchado en `scrapers/prestashop.py`. Generalizado (reutilizando `_parse_product_detail`'s selector `h1`) y re-scrapeado Distrito Zero en vivo: 0 nombres truncados de 66 productos (antes 20). Impacto: 14 filas.

### 16. Huecos de catálogo con demanda real de 2+ tiendas — pendiente de decisión, no de código

Encontrados al llegar al fondo de la cola (49 filas): dos productos que NINGUNA tienda podría matchear porque no están en `data/one_piece_tcg_products.json` en absoluto, con demanda confirmada por más de una tienda cada uno —

- **"Premium Card Collection - Uta"**: la venden Pokemillon Y FreakCorp.
- **"One Piece Day 2024/2025 Premium Card Collection"**: la venden Pokemillon Y Golden Pulls.

Exactamente la señal que `matcher.find_missing_canonical_candidates()` está diseñada para detectar. Pendiente: ejecutarla contra la BBDD actual y decidir si se siembran a mano (mismo criterio que el punto 8, `promo-card`). Sin tocar en esta ronda.

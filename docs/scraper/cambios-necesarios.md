# Cambios necesarios en `store_monitor` (Cartitas) para cumplir el diseño

Basado en la revisión del código real del repositorio (`base_script.py` + `scrapers/`) contra el modelo de datos, los estándares de scraping y la arquitectura ya diseñados. Organizado por bloques, de menor a mayor esfuerzo.

---

## A. Cumplimiento de estándares de scraping (documento `docs/api/estandares.md` / sección "Estándares y buenas prácticas")

### A.1. User-Agent identificable

**Tensión real a resolver, no solo "cambiar el string"**: un User-Agent honesto (`CartitasPriceWatch/1.0 (+https://tu-dominio/bot-info)`) es lo correcto, pero algunos filtros anti-bot simples bloquean por coincidencia de substring en palabras como "bot"/"crawler"/"spider" — cambiarlo a ciegas podría romper tiendas que hoy funcionan con el UA que imita Chrome.

**Decisión de diseño**: usar un nombre de producto propio sin palabras gatillo obvias (`CartitasPriceWatch/1.0` en vez de `CartitasBot/1.0`), pero manteniendo la URL de contacto — es identificable y rastreable sin activar los filtros más ingenuos. Si tras el cambio alguna tienda concreta empieza a bloquear, esa tienda pasa a una excepción documentada (no se vuelve a mentir globalmente por una tienda puntual).

La huella TLS (gestionada por `cloudscraper`, que imita Chrome a ese nivel) **no cambia** — el estándar de "identificable" se refiere solo a la cabecera `User-Agent`, no a hacerse pasar por otra cosa a nivel de protocolo.

### A.2. `robots.txt` / `Crawl-delay`

- **Cuándo se comprueba**: una vez por tienda, cacheado (robots.txt cambia poco — revisar, por ejemplo, una vez por semana es razonable, no en cada scrape).
- **Qué UA-agent del robots.txt se respeta**: como nuestro UA es propio y la mayoría de robots.txt no tendrán una regla específica para él, se aplican las reglas del user-agent comodín (`*`).
- **Si el `Disallow` bloquea justo el path que necesitamos** (la categoría o el sitemap): la tienda se marca como excluida con motivo explícito en logs — **no se ignora el robots.txt para seguir scrapeando de todos modos**. Es la diferencia entre "estándar que se sigue" y "estándar que se consulta pero se salta si molesta".
- **`Crawl-delay`**: si existe, se usa como delay mínimo entre peticiones a esa tienda — `max(DEFAULT_DELAY, crawl_delay_encontrado)`, nunca por debajo del delay que ya tenías.
- Campos afectados en `store`: `crawl_delay_seconds` (ya existía en el esquema) + **nuevo campo `robots_checked_at`** para saber cuándo toca refrescar la caché del robots.txt (no confundir con `last_scraped_at`, que es de la categoría/producto, no del robots.txt).

### A.3. `429 Too Many Requests` + `Retry-After`

- Si la respuesta 429 trae cabecera `Retry-After` (puede venir como segundos o como fecha HTTP), **se espera exactamente ese tiempo** en vez del backoff exponencial genérico — es una instrucción explícita del servidor, tiene prioridad sobre nuestra estimación.
- **Tope máximo de espera** (ej. 5 minutos): si un servidor pide esperar más que eso (mal configurado o intencionadamente hostil), no se bloquea el proceso completo esperando — se corta ahí y se trata como fallo normal de esa tienda en este ciclo.
- Si no trae `Retry-After`, se mantiene el backoff exponencial actual sin cambios.
- **Conexión con el nivel de tienda**: al tercer fallo seguido (ya sea 429 con o sin `Retry-After`), se fija `store.backoff_until` — esto hace que **el siguiente ciclo de scraping** (no solo los reintentos dentro de la misma ejecución) también respete la pausa, algo que hoy no ocurre porque `STORES` es una lista en memoria sin estado entre ejecuciones.

### A.4. Peticiones condicionales (`ETag` / `If-Modified-Since`)

- **No aplica al barrido diario completo** (ese siempre quiere el estado actual de todo, no tiene sentido condicionar). Aplica a los dos mecanismos de alta frecuencia que sí dependen de "solo dime si cambió": polling de sitemap y refresco de producto caliente.
- Flujo: se guarda `last_etag`/`last_modified_header` de la respuesta anterior en `store_product`; en la siguiente petición a esa misma `store_url` se envían como `If-None-Match`/`If-Modified-Since`.
- Respuesta `304 Not Modified` → no hay nada que procesar, solo se actualiza `last_checked_at` (barato). Respuesta `200` → se procesa normal y se guardan los nuevos valores de cabecera para la próxima vez.
- Si una tienda no soporta estas cabeceras (no las devuelve), simplemente no hay nada que enviar la próxima vez — degrada con gracia, sin necesitar detección especial.

### A.5. WooCommerce Store API antes que HTML
✅ Ya implementado — sin cambios.

### A.6. JSON-LD como extracción preferente
Ya cubierto en Odoo y Genérico JSON-LD. Extenderlo a PrestaShop es una mejora posible pero no confirmada (habría que comprobar tienda por tienda si el tema IQIT expone JSON-LD) — se deja como mejora oportunista, no como cambio obligatorio de este bloque.

---

## B. Persistencia real en PostgreSQL (hoy: CSV + hook opcional a SQLite)

El scraper termina en `list[Product]` → CSV. Falta la capa que escribe eso en el esquema Postgres ya diseñado.

### B.1. Cómo conviven `STORES` (código) y `store` (base de datos)

No se trata de elegir entre "todo en código" o "todo en base de datos" — cada campo tiene un sitio natural distinto:

- **`STORES` en código sigue siendo la fuente de verdad para la configuración estática** (nombre, plataforma, URL, selectores/slugs de categoría) — es lo que ya funciona hoy y no hay motivo para duplicarlo a mano en la base de datos.
- **Al arrancar cualquier proceso, se sincroniza `STORES` → tabla `store`** vía `UPSERT` (por `website_url`, que es estable): inserta tiendas nuevas, actualiza los campos estáticos si cambiaron en código. Añadir una tienda nueva a `STORES` la da de alta automáticamente en la base de datos la próxima vez que corra algo, sin paso manual.
- **Los campos dinámicos** (`last_scraped_at`, `last_sitemap_checked_at`, `backoff_until`, `crawl_delay_seconds`, `robots_checked_at`) **viven solo en la base de datos** — la sincronización nunca los toca ni los pisa, porque no existen en `STORES`.

### B.2. Detección de transición de stock (para restock)

- Se lee el `stock_status` **anterior** de `store_product` antes de sobrescribirlo con el nuevo valor scrapeado.
- Solo cuenta como restock si el anterior era `agotado` y el nuevo es `disponible` — **no** si el `store_product` es nuevo (no había fila previa): un listado que aparece por primera vez no es un "restock", es un alta, y no debería disparar notificaciones a nadie (nadie pudo suscribirse a algo que no existía).
- `desconocido` → `disponible`: **decidido no disparar notificación** (2026-08-26) — `desconocido` significa que no sabíamos el estado real, no que estuviera confirmado agotado, así que "ahora está disponible" no es necesariamente una novedad. Ante la duda, no se notifica.

### B.3. Atomicidad

La escritura de `store_product` + `price_history` + (si aplica) `restock_event` para un mismo producto debe ir en una única transacción — si algo falla a medio camino, no debe quedar `price_history` escrito sin su `store_product` actualizado, ni un restock a medias.

### B.4. Rendimiento de escritura por lotes

Con 56+ tiendas y potencialmente miles de productos por barrido diario, escribir fila a fila (una sentencia + commit por producto) sería innecesariamente lento. Mejor: acumular los `Product` de una tienda y escribirlos en un único `INSERT ... ON CONFLICT` con múltiples filas (o `execute_values` si se usa `psycopg2`), una transacción por tienda en vez de una por producto.

El hook opcional `price_history.py` (mencionado en el README como "no incluido todavía") queda sustituido por esta integración — no hace falta mantenerlo como módulo aparte/opcional, la escritura a Postgres pasa a ser parte central del flujo, no un añadido.

---

## C. Motor de matching: de "clasificar" a "vincular a producto canónico"

`classify_product()` ya hace la mitad del trabajo (extrae `product_type`, `set_code`, `main_set`, `language` por reglas) — es la base del matcher automático que diseñamos, pero hoy no hay ningún `product` canónico contra el que comparar.

### C.1. Siembra del catálogo — semi-automatizable, no solo manual

La idea original era sembrar `product` a mano por lanzamiento de set. Con `classify_product()` ya disponible, se puede asistir ese proceso: cuando varios `store_product` distintos comparten el mismo `(main_set, product_type, language)` y **ninguno** tiene un `product` candidato razonable, es una señal fuerte de "falta un producto canónico". El panel de revisión podría mostrar esto como una sugerencia de alta ("6 tiendas venden algo que parece OP17 Booster Box EN y no existe en el catálogo — ¿lo creamos?") en vez de que tengas que anticiparte manualmente a cada lanzamiento.

### C.2. Umbral de confianza — decidido

Para que quede algo implementable, no solo "alto/medio/bajo":
- **Coincide `main_set` + `product_type` + `language` exactamente, y `similarity(name_canonical, name) > 0.6`** → `match_status = 'confirmed'` automático.
- **Coincide `main_set` + `product_type`, pero `similarity` entre 0.35 y 0.6** (o el idioma no coincide/no se detectó) → `needs_review`.
- **`similarity < 0.35` o no coincide ni `main_set` ni `product_type`** → `unmatched`, sin sugerencia útil que mostrar.

Aprobados como valores de partida (2026-08-26). Se calibrarán con datos reales una vez migrado — no hay forma de acertarlos a priori sin ver la distribución real de similitudes, pero arrancan como la política vigente del matcher, no como una propuesta abierta.

**Corregido (2026-08-27, revisión de la cola de matching con datos reales):** el campo de comparación pasó de `main_set` a `set_code` -- `main_set` solo está poblado para la familia `OP` por diseño (ver D.1 más abajo, "distinto de `set_code` para Starter Deck/Illustration Box con código propio"), así que para el resto de familias (`ST`/`DP`/`EB`/`PRB`/`DF`/Illustration Box/Playmat) el chequeo pasaba en falso (`None == None`) y no protegía nada de verdad. `set_code` sí cubre las 6 familias con el MISMO valor que `main_set` tenía para `OP`, así que no cambia ese caso y cierra el hueco para el resto. Detalle completo en el docstring de `matcher._evaluate()`.

**Corregido (2026-08-27, análisis de métricas sobre un scrape completo real de 53 tiendas):** dos ajustes más al matcher, sobre el mismo dataset:

- **Idioma no detectado → se asume Inglés**, no ambigüedad. Antes, "el idioma no coincide/no se detectó" (línea de arriba) trataba "no sé el idioma" igual que "sé que no coincide", y bloqueaba el `confirmed` automático -- medido: 190 de 836 `needs_review` tenían `set_code` exacto y `similarity > 0.6` (la condición de confirmación) pero se quedaban sin confirmar solo por esto, ya que la mayoría de tiendas no escribe "Inglés"/"EN" porque es la variante que venden por defecto (cuando venden JP, SIEMPRE lo marcan). Única excepción: si el texto dice explícitamente "Non-English" (catálogos de promos reales), se deja en ambiguo -- ahí sí hay información y es la contraria. Resultado tras aplicar: `confirmed` pasó de 35 a 226 sobre el mismo dataset. Detalle en el docstring de `classify_product()` (`shared/shared/classify.py`).
- **Candidato en OTRA categoría con el mismo `set_code` exacto SÍ cuenta como "ya existe"** -- antes, `_best_candidate()` y `find_missing_canonical_candidates()` buscaban solo dentro de la categoría que `classify_product()` derivó del texto. Caso real (histórico, pre-Recognition Pipeline -- ver nota abajo): "PRB02 The Best vol.2" se sembró en `premium-collection`, pero varias tiendas lo listan como "Premium Booster PRB-02 Caja" (sin "vol"/"the best" en el texto), que `classify_product()` clasificaba entonces como `BOOSTER_BOX` -- categoría vacía para ese `set_code`, sin este fallback nunca se encontraba el canónico real. El fallback sigue pasando por el MISMO umbral de similitud de texto de siempre (no lo salta) -- solo amplía dónde buscar el candidato, nunca decide un match sin la comprobación de texto habitual.

  **Nota (2026-08-30):** con el Recognition Pipeline (`docs/propuestas/guia_nuevo_matcher.md`), un código `PRB-NN` explícito ya activa directamente la família `PREMIUM_BOOSTER_BOX` (sin depender de "vol"/"the best" en el texto) -- este caso concreto ya no dispara el fallback. El mecanismo en sí sigue existiendo y sigue siendo necesario (p.ej. un canónico sembrado por error en `premium-card-collection` en vez de `premium-booster-box`), solo cambió el ejemplo que lo motivó originalmente.

Decisión explícita tomada junto con lo anterior: **NO** se hizo que un `set_code` exacto salte el umbral de similitud de texto por sí solo (aunque hubiera 377 filas `unmatched` con `set_code` exacto y texto poco parecido que se beneficiarían) -- un mismo `set_code` lo comparten cartas sueltas, promos, booster pack, booster box y double pack del mismo lanzamiento, así que no es señal suficiente para decidir identidad sin la similitud de texto.

### C.3. Candidatos múltiples y empatados — decidido: top-3

Cuando dos productos canónicos tienen `similarity` parecida (ej. edición normal vs. edición especial del mismo set, con nombres casi idénticos), el matcher no fuerza una única sugerencia — se calcula el **top-3** candidatos por similitud y el panel de revisión los muestra todos, para que la persona elija en vez de solo confirmar/rechazar una opción.

**Decidido (2026-08-26)**: `GET /matches/pending` devuelve una lista corta de candidatos con su score cada uno, en vez de un único `suggested_product_id`. El esquema ya refleja esto — se eliminó la columna `store_product.suggested_product_id` (ver `schema-postgresql-app-tcg.sql`), porque guardar una única sugerencia dejó de tener sentido con el top-3: el candidato se calcula en caliente en el propio endpoint vía `ORDER BY similarity(name_canonical, raw_name) DESC LIMIT 3` sobre `idx_product_name_trgm`, evitando que quede una sugerencia guardada y obsoleta en cuanto se da de alta un producto canónico nuevo más parecido. `match_confidence` se conserva, pero pasa a significar "score del match ya confirmado", no el de una sugerencia pendiente.

### C.4. Gestionar expectativas: esto no es aprendizaje automático

`classify_product()` es determinista (reglas fijas de texto) y `pg_trgm` es similitud de texto, no un modelo que mejora solo con el uso. Si el panel de revisión revela un patrón de error recurrente (ej. una tienda que siempre nombra algo de forma rara y el matcher falla ahí sistemáticamente), la corrección es **editar `CLASSIFICATION_RULES` a mano**, no reentrenar nada. Vale la pena tenerlo claro para no esperar que el sistema "aprenda" sin intervención.

### C.5. Exclusión de tipos no comparables (ver también D.3)

`LOTE_CARTAS` y `OTROS` no deberían entrar en este pipeline en absoluto — un lote es por definición una combinación única de cartas, no existe un "producto canónico" razonable con el que compararlo entre tiendas. Se detalla la propuesta de esquema en D.3.

---

## D. Ajustes al esquema de datos, aprendidos del código real

### D.1. Separar `set_code` de `main_set`

Hoy el esquema Postgres solo tiene `set_code`. El código real distingue correctamente el código específico (ej. `ST21`) del set numerado al que pertenece (ej. `OP16`), que son conceptos distintos (un Starter Deck del lanzamiento de OP16 no es "OP16" en sí mismo). No es una relación de clave foránea — son dos campos independientes pero correlacionados (para un Booster Box, `set_code` y `main_set` suelen coincidir; para un Starter Deck, no). Añadir `main_set VARCHAR(10)` a `product`, con su propio índice, para poder filtrar/agrupar por lanzamiento aunque el producto en sí sea un Starter Deck o Illustration Box con su propio código.

### D.2. Poblar `category` con los 13 tipos reales, en jerarquía

En vez de los 4-5 de ejemplo que usamos al diseñar el esquema, la jerarquía real a poblar:

```
Sellado
├── Booster Box
├── Booster Pack
├── Starter Deck
├── Illustration Box
├── Premium Collection
├── Double Pack
├── Mystery Pack
├── Devil Fruits Collection
├── Learn Deck
└── Promo Card
Accesorios
├── Playmat
└── Dice / Accessory
```

`Lote de cartas` y `Otros` quedan **fuera de la jerarquía de categorías comparables** — ver D.3.

**Hecho (2026-08-27, `docs/matching/motor-matching.md` 1.3/1.4):** el árbol de arriba quedó desactualizado -- son 14 tipos, no 13. `Booster Case` se añadió como hijo de `Sellado` (antes "Case" se quedaba sin categoría, atascado en `needs_review`). `Promo Card` se movió de hijo de `Sellado` a hijo de un padre nuevo `Single Card` (junto a `Sellado`/`Accesorios`) -- una carta individual no es "producto sellado" en el mismo sentido que una caja/sobre. Ver `seed-catalog-app-tcg.sql` para el árbol real actual.

### D.3. Nuevo valor en `match_status_enum`: `not_applicable`

`LOTE_CARTAS` y `OTROS` (del punto C.5) no deberían generar trabajo de revisión indefinidamente en la cola — no es que "todavía no se han revisado" (`unmatched`), es que **nunca van a tener un producto canónico razonable**, son ruido esperado del scraping, no trabajo pendiente. Se propone añadir `not_applicable` al ENUM `match_status_enum` (hoy: `unmatched` / `needs_review` / `confirmed`), y que el propio pipeline de matching asigne ese valor automáticamente cuando `product_type` sea `LOTE_CARTAS` u `OTROS`, sin pasar nunca por la cola de revisión. El dato bruto se sigue guardando en `store_product` igual — solo se excluye del flujo de matching.

### D.4. Normalizar el `stock_status`

A los valores exactos del ENUM de Postgres (`disponible` / `agotado` / `desconocido`, en minúscula) al escribir — el código actual parece usar mayúsculas (`AGOTADO`, `DESCONOCIDO`) en algunos puntos; hay que decidir una convención única y aplicarla en el punto de escritura a base de datos (no hace falta cambiar el código interno del scraper, solo la capa de traducción al escribir).

### D.5. Añadir `raw_variant` (nullable) a `store_product`

El campo `variant` del `Product` actual (título de variante Shopify: idioma, grading...) no tiene equivalente en el esquema. Aunque el idioma ya se extrae a `language`, conservar el texto crudo de la variante ayuda a depurar casos como el de Pokemillon (documentado en el README: dos filas del mismo producto por variante, cada una con su propio stock) sin perder información.

### D.6. Añadir `store_sku` (nullable) a `store_product`

Hoy el esquema solo tiene `store_url` como clave de deduplicación. El scraper ya captura `id_product`/`sku` por plataforma; conservarlos da una clave de respaldo más estable que la URL (que puede cambiar si la tienda reestructura su catálogo) para detectar que es "el mismo listado".

---

## E. Piezas nuevas para completar la arquitectura (no son cambios sobre lo existente, son módulos que faltan)

### E.1. Programación de los 4 modos de barrido — decidido: APScheduler

Hoy todo se dispara con `python base_script.py` a mano. **Decidido (2026-08-26)**: en vez de cron del sistema operativo, se consolidan los tres modos en un único proceso Python persistente con `APScheduler`:
- `run_all_stores()` → diario
- Polling de sitemap (módulo nuevo, no existe código todavía) → cada 1-2h
- Refresco de calientes (reutilizando `query_store()`) → cada 2-4h

Ventaja frente a cron externo: control programático sobre los jobs (pausar/reprogramar en caliente, por ejemplo subir la frecuencia del barrido completo durante la ventana de lanzamiento de un set sin editar crontab), y un único proceso que comparte conexión a BBDD y configuración en vez de tres invocaciones aisladas de `python base_script.py`. Contrapartida asumida: un proceso más que mantener vivo (necesita supervisión — systemd, Docker con `restart: always`, o similar — para sobrevivir a reinicios), a diferencia de cron que ya viene gestionado por el sistema operativo.

### E.2. Selección de productos calientes — consulta concreta

```sql
SELECT store_product.*
FROM store_product
JOIN product ON product.id = store_product.product_id
WHERE product.is_hot = true
  AND (product.hot_until IS NULL OR product.hot_until >= CURRENT_DATE)
```

Cada fila resultante pasa por el mismo camino que `query_store()` pero a nivel de producto individual (no de tienda completa) — reutilizando las cabeceras condicionales del punto A.4 para que la mayoría de estas comprobaciones frecuentes sean baratas (`304 Not Modified`).

### E.3. Disparador de notificación de restock — flujo completo

1. Al detectar la transición (B.2), consultar `restock_subscription WHERE product_id = X AND (store_id IS NULL OR store_id = <tienda que detectó el restock>)`.
2. Por cada suscripción activa, construir el payload y enviarlo con `pywebpush`, firmado con las claves VAPID.
3. Registrar el resultado en `restock_event` (`subscribers_notified` = nº de envíos exitosos).
4. **Manejo de suscripciones muertas**: si `pywebpush` devuelve `410 Gone`, significa que el usuario revocó el permiso o desinstaló — esa `restock_subscription` debe **borrarse automáticamente** en ese momento, no seguir intentando enviarle en el futuro. Es parte del propio estándar Web Push, no una decisión nuestra: un 410 es la forma estándar en que el servicio push te dice "no vuelvas a intentarlo con este endpoint".

---

## Resumen de prioridad sugerida (no vinculante, para decidir orden de trabajo)

1. **B** (persistencia Postgres) — sin esto, nada de lo demás tiene dónde vivir.
2. **D** (ajustes de esquema) — mejor hacerlo a la vez que B, antes de que haya datos reales que migrar.
3. **C** (matching canónico) — depende de que el catálogo `product` ya exista poblado.
4. **A** (estándares de scraping respetuoso) — importante pero no bloqueante para que el sistema funcione end-to-end una primera vez.
5. **E** (scheduling, calientes, notificaciones) — la capa que da vida a las decisiones de frecuencia adaptativa y restock, pero requiere que B/C ya estén en pie.

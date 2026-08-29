# Store Monitor

Scraper unificado de precios y stock de **One Piece Card Game** en tiendas online españolas. Recorre 55 tiendas repartidas en 6 plataformas distintas, normaliza todos los productos a un esquema común, y genera un CSV comparable tienda-por-tienda (precio mínimo por set, disponibilidad, tipo de producto...).

## Índice

- [Instalación](#instalación)
- [Uso](#uso)
- [Arquitectura](#arquitectura)
- [Plataformas soportadas](#plataformas-soportadas)
- [Cómo añadir una tienda nueva](#cómo-añadir-una-tienda-nueva)
- [Robustez: timeouts, reintentos, circuit breaker](#robustez-timeouts-reintentos-circuit-breaker)
- [Estándares de scraping respetuoso](#estándares-de-scraping-respetuoso)
- [Consulta puntual de una tienda](#consulta-puntual-de-una-tienda)
- [Salida](#salida)
- [Persistencia en PostgreSQL](#persistencia-en-postgresql)
- [Matching a producto canónico](#matching-a-producto-canónico)
- [Refresco individual de producto (E.2) y polling de sitemap (E.1)](#refresco-individual-de-producto-e2-y-polling-de-sitemap-e1)
- [Notificaciones de restock (E.3)](#notificaciones-de-restock-e3)
- [Orquestación (scheduler.py, E.1)](#orquestación-schedulerpy-e1)
- [Tests](#tests)
- [Limitaciones conocidas](#limitaciones-conocidas)
- [Estructura de archivos](#estructura-de-archivos)

## Instalación

```bash
pip install -r requirements.txt
```

Dependencias: `requests` (HTTP), `cloudscraper` (bypass de Cloudflare para PrestaShop/WooCommerce/Odoo), `beautifulsoup4` (parseo HTML), `pybreaker` (circuit breaker), `psycopg2-binary` (persistencia en PostgreSQL, ver [Persistencia en PostgreSQL](#persistencia-en-postgresql)).

Para levantar Postgres en local (desarrollo):

```bash
docker compose -f docker_composes/docker-compose.yml up -d   # desde la raíz del repo -- aplica schema-postgresql-app-tcg.sql automáticamente
```

## Uso

```bash
python base_script.py
```

Esto:
1. Scrapea las 55 tiendas de `STORES` **en paralelo** (un hilo por tienda).
2. Escribe `multi_tienda_one_piece.csv` con todos los productos encontrados.
3. Si alguna tienda falló, escribe `tiendas_fallidas.csv` con el motivo.
4. Imprime un resumen por consola: filas por tienda, totales por tipo de producto, totales por disponibilidad, y el precio mínimo de cada set (OP-XX) tienda por tienda.
5. Si existe `price_history.py` (módulo opcional, no incluido todavía), guarda un snapshot en SQLite para histórico de precios. Si no existe, el script sigue funcionando igual — solo se pierde ese histórico.

Tarda entre 2 y 3 minutos las 55 tiendas (las que usan `Platform.ODOO` o `Platform.GENERIC_JSONLD` son las más lentas: visitan cada producto individualmente, no solo el listado).

## Arquitectura

Refactorizado (2026-08-26, ver `docs/estandares_organizacion_codigo.md`) en capas de dependencia unidireccional -- cada módulo solo depende de los de su izquierda, nunca al revés, así que ya no hace falta ningún import diferido dentro de una función para evitar un ciclo:

```
shared/(domain.py, classify.py) → config.py → persistence.py → store_state.py → http_client.py → dispatcher.py → scrapers/ → base_script.py
```

`domain.py` y `classify.py` viven en el paquete hermano `shared/` (no aquí dentro), porque `api/` también los necesita -- Shared Kernel de DDD, ver decisión de arquitectura sobre el acoplamiento entre `api/` y `store_monitor/`. `store_monitor/requirements.txt` declara `shared` como dependencia editable (`-e ../shared`).

```
shared/domain.py  -- Platform (enum), StoreConfig, Product, Classification, RefreshedVariant,
                      RefreshOutcome: dataclasses puros, cero imports internos del proyecto.
config.py         -- STORES (las 55 tiendas configuradas -- Arte9 excluida a petición expresa, ver comentario en config.py), IDENTIFIABLE_USER_AGENT y demás
                      constantes de identidad HTTP (A.1), OUTPUT_CSV/FAILED_STORES_CSV.
shared/classify.py -- classify_product() / _detect_language() (deduce tipo/set/idioma del
                      nombre), parse_price_text() / parse_price_minor_unit(). Lógica pura,
                      sin red ni BBDD.
http_client.py    -- build_session() / request_with_retries() (reintentos + backoff + A.3/A.4),
                      StoreLogger, conditional_headers(). Capa HTTP compartida por dispatcher y scrapers.
dispatcher.py     -- scrape_store()/query_store()/run_all_stores(), robots.txt (A.2, cacheado
                      vía store_state), circuit breaker por tienda, backoff persistido entre
                      ejecuciones (A.3).
scrapers/         -- un archivo por plataforma; SCRAPER_CLASSES (registro Platform -> clase)
                      vive en scrapers/__init__.py porque tanto dispatcher.py como
                      persistence.py (refresh_hot_products, E.2) lo necesitan.
base_script.py    -- entry point (`python base_script.py`): junta dispatcher + persistence +
                      matcher + restock_notifier para el barrido batch completo, CSV, resumen.

scrapers/
├── base.py            -- BaseStoreScraper (interfaz común)
├── shopify.py          -- JSON público
├── prestashop.py       -- HTML (tema IQIT)
├── woocommerce.py      -- Store API JSON + fallback HTML
├── odoo.py             -- HTML + JSON-LD por producto
├── opencart.py         -- HTML (tema por defecto)
└── generic_jsonld.py   -- listado configurable + JSON-LD por producto (CMS a medida)
```

**Por qué está separado así:** cada capa tiene una única responsabilidad y solo puede depender de las capas por debajo de ella (ver la regla completa en `docs/estandares_organizacion_codigo.md`, sección 2) -- añadir una tienda solo toca `config.py`, cambiar cómo se reintenta una petición solo toca `http_client.py`, etc. `scrapers/` tiene un archivo por plataforma, cada uno con la lógica específica de cómo esa plataforma expone su catálogo; un scraper nuevo solo necesita implementar `scrape() -> list[Product]` y depende únicamente de `shared.domain`/`http_client.py`/`shared.classify`, nunca del dispatcher.

### El flujo de una tienda

1. `StoreConfig` describe la tienda: label, dominio, plataforma, y los campos específicos de esa plataforma (p.ej. `shopify_collection` o `woocommerce_category_slug`). Se valida sola al crearse — si falta un campo imprescindible, el script no arranca (ver [Cómo añadir una tienda nueva](#cómo-añadir-una-tienda-nueva)).
2. `scrape_store()` busca en `SCRAPER_CLASSES` la clase de esa plataforma, la instancia, y llama a `.scrape()`.
3. Cada scraper devuelve `list[Product]` ya clasificados (`classify_product()` corre dentro de `BaseStoreScraper._make_product()`, así que ningún scraper tiene que acordarse de llamarlo).
4. El dispatcher (`run_all_stores()` o `query_store()`) envuelve esa llamada con timeout y captura de excepciones, y la convierte en un resultado que nunca revienta el proceso completo.

## Plataformas soportadas

| Plataforma | Cómo obtiene los datos | Necesita sesión anti-bot |
|---|---|---|
| **Shopify** | JSON público `/collections/<handle>/products.json` | No |
| **PrestaShop** | HTML del listado de categoría (tema IQIT) | Sí (cloudscraper) |
| **WooCommerce** | Store API JSON (`/wp-json/wc/store/v1/products`), con fallback a HTML del tema Storefront | Solo el fallback |
| **Odoo** | HTML del listado + una petición por producto para leer su JSON-LD (schema.org) | Sí |
| **OpenCart** | HTML del listado (tema por defecto) | Sí |
| **Genérico JSON-LD** | Listado configurable (categoría o búsqueda interna) + JSON-LD por producto | Sí |

### Shopify

La más simple: cualquier tienda Shopify expone su colección como JSON público, sin necesidad de parsear HTML ni de sesión anti-bot. Se pagina con `?limit=250&page=N` hasta que una página viene vacía. Cada **variante** de un producto (idioma, edición...) genera su propia fila, porque el stock se controla a nivel de variante, no de producto.

### PrestaShop

No existe una API pública equivalente a la Store API de WooCommerce, así que se parsea el HTML del listado de categoría directamente (selectores del tema IQIT: `article.product-miniature`). Se detecta el número total de páginas a partir de los enlaces de paginación reales, con dos protecciones:
- Si una página no aporta productos nuevos (deduplicado por `id_product`), se corta la paginación — algunas tiendas re-sirven la última página real para números de página que ya no existen, en vez de dar un 404.
- Límite duro de 50 páginas como red de seguridad.

### WooCommerce

La plataforma con más variantes de scoping (ver `StoreConfig`):
- `woocommerce_category_slug`: filtro de categoría, tanto en la Store API como en post-filtro local.
- `woocommerce_name_must_include`: para tiendas sin categoría estándar fiable (filtran por una taxonomía custom, p.ej. Elementor) — exige que ciertas subcadenas aparezcan en el nombre del producto.
- Ambos se pueden combinar con AND (categoría amplia + nombre debe contener "one piece").

El flujo es: probar la Store API (dos nombres de endpoint posibles, el moderno `/v1/` y uno legacy) → si no da productos (ni disponible, ni con resultados) → fallback a HTML del tema Storefront, siguiendo el enlace real de "siguiente página" (nunca construido a mano, porque cada tema pagina distinto).

**Importante:** si la API responde pero con 0 productos, el scraper **también** prueba el fallback HTML antes de aceptar ese 0 como definitivo — se detectó un caso real (Arte9) donde el endpoint legacy de la Store API respondía 200 con JSON válido pero sin soportar bien el filtro de categoría, mientras que el HTML sí tenía el catálogo completo.

### Odoo

Dos fases:
1. Recorre el listado de categoría recolectando URLs de producto (selectores `.oe_product_cart`).
2. Visita **cada producto individualmente** y lee el bloque `<script type="application/ld+json">` de tipo `Product` que Odoo genera para SEO — es la única fuente fiable de precio y disponibilidad real, porque el HTML del listado no trae esa información (se resuelve por JavaScript en el cliente).

Esto es más lento que las demás plataformas (una petición extra por producto), pero es la única forma de obtener el stock real sin un navegador headless.

Protección contra bucles infinitos: si una página del listado no aporta ninguna URL de producto nueva, se corta la paginación (se detectó una tienda real que re-sirve su última página indefinidamente sin decir "fin" en su paginador), más un límite de 50 páginas.

### OpenCart

HTML del tema por defecto (`div.product-thumb`). El stock **nunca se adivina**: solo se marca AGOTADO si aparece texto explícito ("agotado", "out of stock"...) en la tarjeta de producto; si no hay ninguna señal, se marca DESCONOCIDO en vez de asumir DISPONIBLE a ciegas.

### Genérico JSON-LD

Para tiendas con CMS a medida que no encajan en ninguna plataforma reconocida, pero que exponen JSON-LD schema.org de tipo `Product` en sus páginas de producto (igual que Odoo). Configurable en `StoreConfig`:
- `jsonld_listing_urls`: una o varias URLs de partida (categoría, o una búsqueda interna).
- `jsonld_product_link_selector`: selector CSS de los enlaces de producto en esas páginas.

La paginación se sigue por enlaces reales encontrados en cada página visitada (numerados o "siguiente"), nunca construida a mano — el mismo algoritmo sirve tanto para una categoría normal como para resultados de un buscador interno.

El parser de JSON-LD contempla variaciones reales encontradas: `@type` en minúscula, oferta anidada un nivel más abajo (`offers.offers.availability`, caso `AggregateOffer`), y un fallback a un `<span class="availability">` o una meta `product:availability` cuando el JSON-LD no trae disponibilidad.

## Cómo añadir una tienda nueva

Añade una entrada a `STORES` en `config.py` con el campo correspondiente a su plataforma. `StoreConfig.__post_init__` (en `shared/domain.py`) falla con un mensaje claro si falta algo imprescindible — esto es intencional, es la corrección estructural de un bug real que tuvo este proyecto (una tienda WooCommerce sin categoría bien acotada acabó trayéndose todo el catálogo de la tienda, no solo One Piece).

```python
# Shopify
StoreConfig("Mi Tienda", "https://mitienda.com", Platform.SHOPIFY,
            shopify_collection="one-piece-tcg"),

# PrestaShop
StoreConfig("Mi Tienda", "https://mitienda.com", Platform.PRESTASHOP,
            prestashop_category_url="https://mitienda.com/62-one-piece"),

# WooCommerce
StoreConfig("Mi Tienda", "https://mitienda.com", Platform.WOOCOMMERCE,
            woocommerce_category_slug="one-piece",
            woocommerce_fallback_paths=("categoria-producto/one-piece",)),

# Odoo
StoreConfig("Mi Tienda", "https://mitienda.com", Platform.ODOO,
            odoo_category_url="https://mitienda.com/shop/category/one-piece-10"),

# OpenCart
StoreConfig("Mi Tienda", "https://mitienda.com", Platform.OPENCART,
            opencart_category_url="https://mitienda.com/index.php?route=product/category&path=77"),

# Genérico JSON-LD (CMS a medida)
StoreConfig("Mi Tienda", "https://mitienda.com", Platform.GENERIC_JSONLD,
            jsonld_listing_urls=("https://mitienda.com/categoria/one-piece",),
            jsonld_product_link_selector="a[href*='/producto/']"),
```

**Antes de dar por buena una entrada nueva**, verifica contra la tienda real (no adivines el slug/URL de categoría a partir del nombre):
- Shopify: `GET /collections/<handle>/products.json?limit=3` debe devolver productos reales.
- WooCommerce: `GET /wp-json/wc/store/v1/products?category=<slug>&per_page=3` debe devolver productos reales, Y localizar la ruta HTML equivalente para el fallback.
- PrestaShop/OpenCart/Odoo/Genérico: cargar la URL de categoría a mano y confirmar que lista productos de One Piece reales, no una categoría genérica que mezcle otros juegos.

Si una tienda WooCommerce no tiene categoría estándar fiable, usa `woocommerce_name_must_include` en vez de forzar un `category_slug` que no existe.

## Robustez: timeouts, reintentos, circuit breaker

- **Reintentos con backoff exponencial + jitter** (`request_with_retries`) en cada petición HTTP: reintenta en errores de red, 429 y 5xx; no reintenta en 404 u otros 4xx (no cambiaría el resultado). En un 429, si el servidor manda `Retry-After` (segundos o fecha HTTP), se espera exactamente ese tiempo en vez del backoff genérico — con un tope de `MAX_RETRY_AFTER_WAIT` (5 min): si pide esperar más, no se bloquea el proceso, se corta y se trata como fallo normal de esa tienda en este ciclo.
- **Bypass automático de un reto anti-bot trivial**: algunas tiendas usan una página intermedia que fija una cookie estática vía JavaScript antes de recargar (verificado en AvalonBurgos). `request_with_retries` lo detecta y lo resuelve solo, sin necesitar un navegador real.
- **Fallback SSL automático**: si la sesión anti-bot (`cloudscraper`, que imita la huella TLS de Chrome) falla con un error SSL, se reintenta una vez con una sesión de `requests` normal antes de rendirse — se detectó una tienda real (Arte9) donde esa huella TLS específica chocaba con la configuración del servidor, mientras que `requests` normal conectaba sin problema.
- **Timeout por inactividad, no por duración total** (`STORE_TIMEOUT = 90s`): una tienda con muchas páginas que sigue respondiendo no se corta; solo se da por caída si pasan 90s SIN ninguna petición enviada/recibida. Cada scraper llama a `logger.touch()` (vía el parámetro `heartbeat` de `request_with_retries`) antes de cada petición.
- **Límite de páginas por tienda** (`MAX_LISTING_PAGES = 50` en Odoo/PrestaShop/Genérico JSON-LD): protección contra paginación mal detectada o circular — se dio un caso real (Odoo) donde una tienda re-servía su última página indefinidamente sin indicar el final, lo que habría colgado el proceso para siempre sin este límite (el timeout por inactividad no lo detecta, porque cada página sí cuenta como actividad).
- **Circuit breaker por tienda** (`pybreaker`, solo en `query_store()`): tras 3 fallos seguidos de la misma tienda, se deja de intentarlo durante ~5 minutos y se devuelve el fallo al instante (`status="circuit_open"`) en vez de esperar el timeout completo en cada consulta. Pensado para cuando una tienda concreta está caída/bloqueada y se sigue consultando repetidamente (p.ej. desde un front). `run_all_stores()` no lo usa porque cada tienda solo se intenta una vez por ejecución batch — en su lugar usa el backoff persistido de abajo.

## Estándares de scraping respetuoso

Ver `cambios-necesarios-scraper.md` (bloque A) para la discusión completa de estas decisiones.

- **User-Agent identificable** (`IDENTIFIABLE_USER_AGENT`): el scraper se identifica como `CartitasPriceWatch/1.0 (+<URL de contacto>)` en vez de imitar un navegador. La URL de contacto (`BOT_CONTACT_URL`) es un placeholder pendiente de sustituir por el dominio real. Si una tienda concreta bloquea este UA, se marca esa `StoreConfig` con `ua_exception=True` (usa entonces `BROWSER_LIKE_USER_AGENT`) — es una excepción documentada por tienda, no un revert global.
- **robots.txt / Crawl-delay** (`get_robots_rules`, `scrape_store`): antes de scrapear se comprueba (y cachea una semana, en `store.robots_checked_at`/`crawl_delay_seconds`/`disallowed`) el robots.txt de la tienda contra la URL de listado que se va a pedir. Si el `Disallow` la cubre, la tienda se excluye ese ciclo con motivo explícito en logs — no se ignora robots.txt para scrapear igualmente. Si declara `Crawl-delay`, se usa como mínimo entre peticiones a esa tienda (nunca por debajo de `DEFAULT_DELAY`).
- **Backoff persistido entre ejecuciones** (`store_state.py`, `_record_backoff_outcome`): a diferencia del circuit breaker de `query_store()` (en memoria, solo dentro del proceso), este cuenta fallos seguidos de `run_all_stores()` en **ejecuciones distintas** (`store.consecutive_failures`/`backoff_until`). Al tercer fallo seguido, la tienda queda en backoff (`STORE_BACKOFF_DEFAULT_SECONDS`, 30 min) y el próximo `run_all_stores()` la salta sin intentarla hasta que pase. Una exclusión por robots.txt no cuenta como fallo.
- **`store.active`** (cableado 2026-08-27, API v1 `PATCH /stores/{id}`): `run_all_stores()` salta por completo cualquier tienda con `active = false`, igual que las que están en backoff. Antes de esto la columna existía en el esquema pero no tenía ningún efecto real.

`store_state.py` (migrado a Postgres 2026-08-26, antes usaba un JSON local) expone la misma interfaz `get_state()`/`update_state()` por dominio de siempre, ahora contra las columnas de `store` en vez de un fichero. Degrada con gracia si Postgres no está disponible: el scraper sigue funcionando igual (sin la caché de robots.txt/backoff ese ciclo, con AVISO en logs), en vez de fallar. Limitación real: en una BBDD recién creada, la fila de `store` de una tienda no existe hasta que corre `sync_stores()` al final del primer ciclo completo — el primer chequeo de robots.txt de una tienda nueva no queda cacheado esa primera vez.

## Consulta puntual de una tienda

Además del batch completo (`main()` / `run_all_stores()`), existe `query_store()` para scrapear **una sola tienda** de forma aislada — pensado como la pieza base de un futuro endpoint ("elige una tienda y consúltala ahora" desde un front):

```python
from base_script import find_store, query_store

config = find_store("Cardzone")   # None si no existe
result = query_store(config)      # nunca lanza excepciones

result.status    # "ok" | "empty" | "timeout" | "error" | "circuit_open"
result.products  # list[Product], vacía salvo status == "ok"
result.error     # motivo legible, o None si status == "ok"
```

`StoreQueryResult` está pensado para poder devolverse tal cual como JSON a un cliente: nunca hay que capturar excepciones ni conocer el mecanismo interno de timeout, solo mirar `status`.

## Salida

- **`multi_tienda_one_piece.csv`**: una fila por producto/variante, con columnas `store, platform, id_product, name, variant, product_type, main_set, set_code, language, price, stock_status, url, sku, image_url`.
- **`tiendas_fallidas.csv`**: `store, platform, motivo` — solo se genera si alguna tienda no dio productos.

## Persistencia en PostgreSQL

Además del CSV, `main()` escribe en PostgreSQL vía `persistence.py` (bloque B de `cambios-necesarios-scraper.md`). Ya no es un hook opcional como el antiguo `price_history.py` mencionado en versiones previas de este README — es parte central del flujo, aunque un fallo de conexión puntual no destruye el CSV (ya se guardó antes).

Variable de entorno `DATABASE_URL` (por defecto apunta al contenedor de `docker-compose.yml`, puerto **5433** — no el 5432, para no chocar con un Postgres del sistema):

```bash
export DATABASE_URL="postgresql://cartitas:cartitas@localhost:5433/cartitas"
```

Dos escrituras, cada ejecución de `main()`:

**Bug real corregido (2026-08-26)**: el `UNIQUE` de `store_product` era solo `(store_id, store_url)` — varias variantes de un mismo producto Shopify (idioma, sobre/caja...) comparten `store_url`, así que la segunda variante pisaba a la primera en vez de convivir como fila propia (verificado en Pokemillon: 130 de 430 productos, un 30%, colapsaban). Ahora es `(store_id, store_url, raw_variant)` — si aplicas el esquema sobre una BBDD ya creada con el `UNIQUE` viejo, hace falta el `ALTER TABLE` (ver `schema-postgresql-app-tcg.sql`, no se migra solo).

1. **`sync_stores()`** — `STORES` (código) → tabla `store`, `UPSERT` por `website_url`. Solo toca los campos ESTÁTICOS que existen en `StoreConfig` (`name`, `platform`); los dinámicos (`crawl_delay_seconds`, `backoff_until`, `last_scraped_at`, `robots_checked_at`) viven solo en la BBDD y esta sincronización nunca los pisa.
2. **`persist_scrape_results()`** — los `Product` ya scrapeados → `store_product` + `price_history` + `restock_event`, en **una transacción por tienda** (no por producto, ni una única transacción gigante para las 55): un lote corrupto de una tienda hace `ROLLBACK` solo, sin perder las demás. Cada lote:
   - Lee el `stock_status` **anterior** de `store_product` antes de sobrescribirlo, para detectar la transición `agotado → disponible` (restock). Un `store_url` sin fila previa nunca cuenta como restock (alta nueva, no restock), y tampoco cuenta si no hay `product_id` confirmado todavía (ver matching, abajo).
   - Trunca defensivamente `raw_name`/`raw_variant`/`store_sku` a los límites del esquema si el HTML de una tienda concreta cuela texto anómalo (visto en Arte9, mismo tema Madara de la limitación de más abajo) — con aviso en logs, en vez de que ese único producto tire abajo la transacción de toda la tienda.
   - Normaliza `stock_status` a los valores exactos del enum (minúscula) — el scraper interno sigue en mayúsculas, la traducción vive solo aquí.

## Matching a producto canónico

Tras persistir, `main()` ejecuta `matcher.run_matching()` (bloque C de `cambios-necesarios-scraper.md`): vincula cada `store_product.raw_name` a un `product` canónico, o decide que no se puede vincular todavía. **No es aprendizaje automático** — `classify_product()` es determinista (reglas fijas de texto, las mismas del scraper) y la similitud usa `pg_trgm` sobre `name_canonical`. Si un patrón de error se repite, la corrección es editar `CLASSIFICATION_RULES` a mano, no reentrenar nada.

**Requisito previo**: la tabla `category` tiene que estar sembrada con los 14 tipos reales (D.2) antes de poder matchear nada:

```bash
docker exec -i cartitas-postgres psql -U cartitas -d cartitas < seed-catalog-app-tcg.sql
```

(se aplica automáticamente en un contenedor nuevo, vía `docker-compose.yml` — solo hace falta a mano si el volumen ya existía de antes).

Con `category`/`game` sembrados, `seed_official_catalog.py` puebla `product` con el catálogo oficial de Bandai (`data/one_piece_tcg_products.json`), reutilizando `classify_product()` para derivar `main_set`/`set_code`/`language` igual que se haría de un `raw_name` real. Idempotente (se puede re-ejecutar sin duplicar):

```bash
python3 seed_official_catalog.py
```

Cada lanzamiento tipo booster se siembra dos veces (Booster Box + Booster Pack) porque las tiendas los venden como SKUs distintos con precios muy distintos. Los productos sin categoría en la taxonomía de 14 tipos (fundas, binders, cajas de almacenaje sueltas, sets de aniversario...) se omiten y se listan al final de la ejecución.

**Hecho (2026-08-28, `docs/pendientes-motor-matching.md`):** cada release booster con multiplicador conocido se siembra TAMBIÉN como `booster-case` (EN+JP) -- `x12` para `booster-box`, `x10` para `premium-collection` (verificado real, no uniforme; `starter-deck`/`double-pack` se dejan sin Case a falta de evidencia). La variante JP (antes solo `booster-box`/`booster-pack`) se amplió a `booster-case`/`double-pack`/`premium-collection`. Además, `BOOSTER_CASE` en `classify_product()` dejó de ser un keyword suelto (colaba accesorios reales como "Limited Card Case -Monkey.D.Luffy-") y pasó a exigir "case" + contexto (código de set o palabra de caja/booster/sellado) en el mismo texto -- validado fila a fila contra las 34 menciones reales de "case" del CSV.

Umbrales (aprobados 2026-08-26 como valores de partida, calibrados con datos reales el 2026-08-27 -- ver detalle completo en `docs/cambios-necesarios-scraper.md` sección C.2):

| Condición | Resultado |
|---|---|
| `set_code` + tipo + idioma exactos, y `similarity > 0.6` | `confirmed` (`product_id` se rellena) |
| `set_code` + tipo coinciden, `similarity` en `[0.35, 0.6)` (o idioma no coincide) | `needs_review` |
| `set_code` NO coincide (aunque el tipo sí), o `similarity < 0.35` | `unmatched` |
| tipo `LOTE_CARTAS` u `OTROS` | `not_applicable` (nunca entra en el pipeline — ver `NOT_APPLICABLE_PRODUCT_TYPES`) |

`set_code`, no `main_set` (corregido 2026-08-27): `main_set` solo está poblado para la familia `OP`, así que para el resto (`ST`/`DP`/`EB`/`PRB`/`DF`/Illustration Box/Playmat) el chequeo pasaba en falso y no protegía nada. `set_code` cubre las 6 familias.

**Hecho (2026-08-27, `docs/implementacion-auto-confirmado-setcode.md`):** la tabla de arriba sigue vigente como camino de respaldo (sin `set_code` exacto en algún lado), pero ahora hay un camino RÁPIDO que confirma sin depender de `similarity` en absoluto: candidato PRIMARIO de su propia categoría (no el fallback cross-categoría de más abajo, marcado `es_fallback` por `_best_candidate`) + `set_code` + idioma exactos + cantidad no ambigua (`cantidad_es_ambigua()` en `shared/classify.py` -- detecta bundles reales como "Pack 5 Sobres" o un Case "x10" frente a la cantidad estándar de la categoría). Validado contra 8 casos reales de confirmación y 8 falsos positivos reales de `multi_tienda_one_piece.csv` (carta promo suelta emparejada por casualidad de código, Case confundido con caja suelta, idioma JP sin canónico sembrado, lanzamiento inexistente en el catálogo). Categoría nueva `booster-case` (antes "Case" se quedaba sin salida en `needs_review`); `promo-card` se reestructuró bajo un padre propio `single-card` en vez de `Sellado`.

**Hecho (2026-08-28, `docs/pendientes-motor-matching.md` punto 6):** `_best_candidate()` desempata también por IDIOMA -- dentro de la MISMA categoría+set_code, el canónico EN y el JP comparten casi todo el texto salvo el sufijo, así que `similarity()` los deja a menudo empatados exactos; sin este criterio el `ORDER BY` caía al orden físico de la tabla, que devolvía casi siempre el EN aunque `classify_product()` ya hubiera detectado JP -- 61 filas reales se quedaban en `needs_review` por esto pese a tener el candidato JP correcto ya sembrado. Va DESPUÉS de `set_code` y ANTES de caja/sobre en el `ORDER BY`. Mismo fix aplicado en paralelo a `api/services/matches.py::_top_candidates()` (la lista de sugerencias que ve el panel), para que las dos implementaciones sigan el mismo criterio.

**Hecho (2026-08-28, `docs/propuesta-mejoras-matching-sesion.md`):** `classify_product()` reconoce `DF-NN` (Devil Fruits Collection) y `DP-NN` (Double Pack) por patrón de código incluso cuando `product_type` habría salido de una `raw_tags` genérica de catálogo (ej. "Cajas") en vez de una keyword real -- el código en el propio nombre/variante es señal más fiable que un tag reutilizado entre productos sin relación (mismo problema que `BOOSTER_CASE`+tags, ver más arriba, pero esta vez el código NO gana si el nombre/variante YA traía una keyword real de otro tipo). También: un rango de códigos (`"ST-15 - ST-20"`, `"[ST-31]~[ST-36]"`) extrae `set_code=None` en vez del primer extremo -- esos packs promocionales están ligados a todo un lote de mazos, no a uno solo, y el extremo suelto disparaba el fallback cross-categoría contra un Starter Deck ajeno. Y `starter-deck` se añadió a `_JP_VARIANT_CATEGORY_SLUGS` (7 ejemplos reales más de demanda JP). Resultado en una pasada real: `needs_review` 182→158 (-13%).

Idioma no detectado ya no es ambigüedad (corregido 2026-08-27): `classify_product()` asume Inglés cuando ni el nombre ni la variante dicen el idioma -- es la variante que cualquier tienda vende por defecto; cuando venden JP, SIEMPRE lo marcan explícito. Única excepción: el texto "Non-English" se deja en ambiguo, no se asume EN. Antes de este cambio, 190 de 836 `needs_review` reales tenían `set_code` exacto y `similarity > 0.6` pero se quedaban sin confirmar solo por esto.

`run_matching()` reevalúa TODO lo que no esté ya `confirmed` en cada pasada (un `confirmed` es una decisión ya tomada, no se revierte sola). El top-3 de candidatos para el panel de revisión **no se guarda** — se calcula en caliente vía `ORDER BY similarity(...) DESC LIMIT 3`, para no arrastrar una sugerencia obsoleta en cuanto se siembra un producto canónico nuevo más parecido. Si dentro de la categoría derivada del texto ningún candidato trae el `set_code` exacto, se repite la búsqueda en TODO el catálogo por ese `set_code` antes de rendirse (caso real: "PRB02 Booster Box" se clasifica como booster-box por texto, pero el canónico vive en premium-collection) — sigue pasando por el mismo umbral de similitud de texto, solo amplía dónde buscar. **Hecho (2026-08-27):** `_best_candidate()` devuelve `(candidato, es_fallback)` -- un candidato que solo aparece por esta búsqueda cross-categoría nunca auto-confirma por el camino rápido de arriba, aunque el resto de condiciones se cumplan (señal más débil a propósito, pensada para sugerir en `needs_review`, no para confirmar sola).

`matcher.find_missing_canonical_candidates()` agrupa lo no confirmado por `(tipo, set_code, idioma)` y reporta combinaciones que varias tiendas distintas venden pero que no tienen ningún candidato en el catálogo (ni en la categoría derivada ni en ninguna otra con ese `set_code`) — pensado como la señal que alimenta la vista `missing-candidates` del panel de revisión, no crea nada automáticamente.

**Limitación real encontrada probando esto, resuelta (2026-08-27)**: Arte9 trunca los nombres en la vista de categoría (`"... . . ."`) antes de llegar al código de set al final del título real — `classify_product()` casi nunca podía extraer `main_set` de esos nombres, así que todo lo que no caía en `not_applicable` quedaba `unmatched`. `WooCommerceScraper._scrape_via_html()` (`scrapers/woocommerce.py`) ahora detecta por patrón (`_looks_truncated()`, no atado a ninguna tienda) cuando un nombre del listado viene recortado y visita la ficha individual del producto para recuperar el título completo vía `.product_title` — la clase que añade el CORE de WooCommerce en la plantilla de producto individual, no depende del tema (a diferencia de `.woocommerce-loop-product__title` del listado, que sí varía y es la que falla en el tema Madara de Arte9). Cualquier otra tienda WooCommerce con el mismo problema de tema se beneficia igual, sin tocar este código.

**Generalizado a PrestaShop (2026-08-29)**: mismo síntoma visto real en Distrito Zero (tema IQIT) — 19 de 20 `raw_name` en revisión de esa tienda terminaban en `"..."` literal. El mecanismo (`_looks_truncated`/visitar la ficha individual, reutilizando el `h1` que ya usa `_parse_product_detail`) nunca se había enganchado en `PrestaShopScraper` pese a que el patrón, por diseño, no depende de la plataforma. Verificado en vivo tras el fix: 0 nombres truncados de 66 productos de Distrito Zero (antes 20).

Auditoría completa de la cola `needs_review` (182 → 49 filas) el 2026-08-29 — lookups nuevos de personaje/título de release, categoría-único-SKU auto-confirma, y varios bugs reales de composición corregidos (regex sin `IGNORECASE`, `raw_tags` reciclado pisando el `name`, código explícito no pisaba un tipo ya mal resuelto). Detalle completo en `docs/pendientes-motor-matching.md` (puntos 6, 10-16) y `tests/README.md`.

## Refresco individual de producto (E.2) y polling de sitemap (E.1)

Además del barrido completo por categoría, cada scraper implementa `refresh_product(config, store_url, *, store_sku=None, etag=None, last_modified=None) -> RefreshOutcome` (método de clase, sin necesitar categoría/logger/delay) para consultar UN producto ya conocido sin recorrer la tienda entera. Reutiliza cabeceras condicionales (A.4: `If-None-Match`/`If-Modified-Since`) cuando el servidor las soporta — si no cambió, `status="not_modified"` (304) y no hay nada que reprocesar.

Verificado contra tiendas reales, plataforma por plataforma:

| Plataforma | Cómo refresca | Limitación real encontrada |
|---|---|---|
| Shopify | `{store_url}.json` | Ninguna — ETag real confirmado (304 funciona) |
| WooCommerce | Store API `/wp-json/wc/store/v1/products/{id}` (necesita `store_sku`, D.6) | Sin `store_sku` (productos del fallback HTML) → `not_supported`. La Store API probada no devuelve ETag/Last-Modified — degrada con gracia (A.4), sin el ahorro del 304 |
| PrestaShop | Microdata `[itemprop=price]`/`[itemprop=availability]` de la ficha (tema IQIT) | `[itemprop=name]` apunta al breadcrumb, no al producto — se usa `h1` en su lugar |
| OpenCart | Selector `.price` de la ficha | **Sin señal de stock fiable** — se probó a reusar las palabras clave del listado sobre el texto completo de la ficha y dio un falso positivo real ("opciones disponibles" del selector de variante). Stock siempre `DESCONOCIDO` aquí, solo se refresca precio |
| Odoo / JSON-LD genérico | Reutiliza el parser JSON-LD que YA usa el barrido normal (ambos visitan la ficha individual de por sí) | Ninguna nueva |

`persistence.refresh_hot_products(conn, stores)` es el orquestador de E.2: consulta `store_product` cuyo `product.is_hot=true` (y no caducado), agrupa por URL (Shopify puede tener varias filas/variantes para la misma URL), llama a `refresh_product()`, y reutiliza `_save_one_store()` para no duplicar la lógica de restock/price_history — reconstruye `Product` "sintéticos" con el `raw_name` YA CONOCIDO (un refresco no redescubre el producto, solo comprueba si cambió).

`sitemap_poller.poll_sitemaps(conn, stores)` es E.1: compara las URLs de `store.sitemap_url` (poblado a mano por tienda, `UPDATE store SET sitemap_url = '...'` — no viene de `STORES`/código) contra las ya conocidas, y extrae puntualmente las nuevas vía `refresh_product()`. **Hallazgo real probándolo en Cardzone**: el sitemap cubre TODA la tienda (~4100 URLs de varios TCG), no solo la categoría scrapeada — hizo falta filtrar por sub-sitemap "product" + prefijo de ruta conocido + palabra clave del juego en el slug, más un tope (`MAX_NEW_URLS_PER_POLL=30`) por seguridad. Aun así, encontró **10 productos reales** que el barrido por categoría nunca ve (viven en otra colección de Shopify: cajas con "embalaje dañado").

## Notificaciones de restock (E.3)

`restock_notifier.notify_for_restock_events(conn, restock_event_ids)` se llama justo después de persistir (barrido diario y refresco de calientes): busca `restock_subscription` activas para cada `restock_event` recién creado (`product_id` + tienda concreta o cualquiera), envía un Web Push firmado con VAPID (`pywebpush`), y **borra automáticamente** las suscripciones que devuelven `410 Gone` (estándar Web Push: el usuario revocó el permiso o desinstaló).

Requiere `VAPID_PRIVATE_KEY_PATH` (ruta a un `.pem`) y `VAPID_CLAIMS_SUB` como variables de entorno — sin ellas, no envía nada con un AVISO en vez de fallar (normal mientras no haya un frontend real registrando suscripciones). Claves de desarrollo:

```bash
python3 -c "from py_vapid import Vapid; v = Vapid(); v.generate_keys(); open('vapid_private.pem','wb').write(v.private_pem())"
export VAPID_PRIVATE_KEY_PATH=vapid_private.pem
```

Verificado con un servidor HTTP local simulando ambas respuestas de un servicio push real (201 = enviado, 410 = suscripción muerta) — no hay forma de probar contra un servicio push real sin un endpoint de navegador de verdad.

## Orquestación (`scheduler.py`, E.1)

Proceso persistente con `APScheduler` (decidido 2026-08-26 frente a cron del sistema, ver `cambios-necesarios-scraper.md`) que sustituye lanzar `python base_script.py` a mano:

```bash
python3 scheduler.py
```

Tres jobs: `barrido_diario` (cron, 1x/día, reutiliza `main()` tal cual), `refresco_calientes` (cada `HOT_REFRESH_INTERVAL_HOURS`, por defecto 3h) y `polling_sitemap` (cada `SITEMAP_POLL_INTERVAL_HOURS`, por defecto 1.5h). Pensado para correr dentro de un proceso supervisado (systemd, Docker con `restart: always`) — se queda en primer plano y no se recupera solo de un crash del propio proceso.

## Tests

434 tests (`pytest`), pirámide invertida a propósito respecto a un proyecto típico: el riesgo real aquí no es "se rompió la lógica de negocio pura" (barata de cubrir con unitarios), sino "una tienda cambió su HTML" o "el SQL no hace lo que creo" -- de ahí el peso en integración con HTTP mockeado y Postgres real, no solo mocks de todo.

```bash
pip install -r requirements-dev.txt

# Los tests de persistencia/matching/store_state/E necesitan una BBDD de test
# SEPARADA de la de desarrollo (mismo contenedor, otra base):
docker exec cartitas-postgres psql -U cartitas -d cartitas -c "CREATE DATABASE cartitas_test;"
docker exec -i cartitas-postgres psql -U cartitas -d cartitas_test < ../schema-postgresql-app-tcg.sql

pytest                          # todo
pytest --cov=. --cov-report=term-missing   # con cobertura
```

Los tests que no piden la fixture `db_conn` no tocan Postgres en absoluto (se saltan solos si `cartitas_test` no está levantada -- no hace falta Docker para la mayoría, los unitarios y de scraper).

**Bug real encontrado escribiendo estos tests** (no un caso feliz más): el `UNIQUE(store_id, store_url, raw_variant)` de `store_product` (añadido en el bloque E para el caso Pokemillon) nunca detectaba conflicto cuando `raw_variant` era `NULL` -- que es el caso de TODAS las plataformas salvo Shopify. Cada re-scrape de una tienda WooCommerde/PrestaShop/OpenCart/Odoo/JSON-LD habría creado una fila duplicada nueva en vez de actualizar la existente, sin límite, cada día. Corregido con `UNIQUE NULLS NOT DISTINCT` (Postgres 15+) -- ver el comentario en `schema-postgresql-app-tcg.sql`.

Desglose fichero por fichero, bugs reales encontrados y qué queda deliberadamente sin cubrir: ver [`tests/README.md`](tests/README.md) -- mantenida como la fuente de verdad detallada, no duplicada aquí para que no se desincronicen las dos.

## Limitaciones conocidas

Documentadas directamente en los comentarios de `STORES` (bloque "Pendientes" al final de la lista):

- **Puerto Fantasy**: bloqueada por un WAF genérico (403/404), sin reto JS que resolver — no es el mismo caso que AvalonBurgos.
- **Hotei Games**: la tienda usa un widget Ecwid que renderiza el catálogo 100% por JavaScript; no viable sin navegador headless.
- Varias tiendas descartadas por tener la categoría de "One Piece" mezclada con merchandising/otros juegos sin forma fiable de acotar (AvalonBurgos, DRAGONCT, Bettoy).
- El fallback HTML de WooCommerce no siempre encuentra el precio si la tienda usa un tema muy personalizado (p.ej. Arte9 usa el tema Madara, cuyo marcado de precio no coincide con los selectores genéricos de Storefront) — en esos casos prioriza que la Store API funcione en vez de perseguir selectores tema a tema.

## Estructura de archivos

```
store_monitor/
├── README.md
├── requirements.txt      -- incluye `-e ../shared` (domain.py/classify.py, ver ../shared/)
├── config.py             -- STORES (55 tiendas) + constantes de identidad HTTP (A.1)
├── http_client.py        -- build_session(), request_with_retries(), StoreLogger, conditional_headers()
├── dispatcher.py         -- scrape_store()/query_store()/run_all_stores(), robots.txt, circuit breaker
├── base_script.py        -- entry point (`python base_script.py`): batch completo + CSV + main()
├── store_state.py        -- estado runtime por tienda entre ejecuciones (robots.txt, backoff) -- columnas de `store`
├── persistence.py        -- escritura a PostgreSQL (store/store_product/price_history/restock_event)
├── matcher.py            -- matching store_product -> product canónico (pg_trgm + reglas)
├── seed_official_catalog.py -- siembra product desde data/one_piece_tcg_products.json
├── sitemap_poller.py     -- E.1: descubrimiento temprano vía sitemap.xml
├── restock_notifier.py   -- E.3: Web Push + VAPID al detectar restock
├── scheduler.py          -- E.1: orquestador persistente (APScheduler)
└── scrapers/
    ├── __init__.py       -- SCRAPER_CLASSES (registro Platform -> clase)
    ├── base.py
    ├── shopify.py
    ├── prestashop.py
    ├── woocommerce.py
    ├── odoo.py
    ├── opencart.py
    └── generic_jsonld.py
```

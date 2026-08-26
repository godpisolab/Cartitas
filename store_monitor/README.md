# Store Monitor

Scraper unificado de precios y stock de **One Piece Card Game** en tiendas online españolas. Recorre 56 tiendas repartidas en 6 plataformas distintas, normaliza todos los productos a un esquema común, y genera un CSV comparable tienda-por-tienda (precio mínimo por set, disponibilidad, tipo de producto...).

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
- [Limitaciones conocidas](#limitaciones-conocidas)
- [Estructura de archivos](#estructura-de-archivos)

## Instalación

```bash
pip install -r requirements.txt
```

Dependencias: `requests` (HTTP), `cloudscraper` (bypass de Cloudflare para PrestaShop/WooCommerce/Odoo), `beautifulsoup4` (parseo HTML), `pybreaker` (circuit breaker), `psycopg2-binary` (persistencia en PostgreSQL, ver [Persistencia en PostgreSQL](#persistencia-en-postgresql)).

Para levantar Postgres en local (desarrollo):

```bash
docker compose up -d   # desde la raíz del repo -- aplica schema-postgresql-app-tcg.sql automáticamente
```

## Uso

```bash
python base_script.py
```

Esto:
1. Scrapea las 56 tiendas de `STORES` **en paralelo** (un hilo por tienda).
2. Escribe `multi_tienda_one_piece.csv` con todos los productos encontrados.
3. Si alguna tienda falló, escribe `tiendas_fallidas.csv` con el motivo.
4. Imprime un resumen por consola: filas por tienda, totales por tipo de producto, totales por disponibilidad, y el precio mínimo de cada set (OP-XX) tienda por tienda.
5. Si existe `price_history.py` (módulo opcional, no incluido todavía), guarda un snapshot en SQLite para histórico de precios. Si no existe, el script sigue funcionando igual — solo se pierde ese histórico.

Tarda entre 2 y 3 minutos las 56 tiendas (las que usan `Platform.ODOO` o `Platform.GENERIC_JSONLD` son las más lentas: visitan cada producto individualmente, no solo el listado).

## Arquitectura

```
base_script.py
├── Platform (enum)          -- las 6 plataformas soportadas
├── StoreConfig (dataclass)  -- config de una tienda; se autovalida en __post_init__
├── STORES (list)            -- las 56 tiendas configuradas
├── Product (dataclass)      -- fila normalizada, idéntica para todas las plataformas
├── classify_product()       -- deduce tipo/set/idioma a partir del nombre
├── parse_price_text() / parse_price_minor_unit()  -- parseo de precios
├── build_session() / request_with_retries()       -- capa HTTP compartida
├── StoreLogger              -- print + marca de actividad + recuerda el último error
├── scrape_store()           -- instancia el scraper de una tienda y lo ejecuta
├── query_store()            -- scrapea UNA tienda de forma aislada (con circuit breaker)
├── run_all_stores()         -- scrapea TODAS las tiendas en paralelo (usado por main())
└── main()                   -- entry point: batch completo + CSV + resumen + SQLite opcional

scrapers/
├── base.py            -- BaseStoreScraper (interfaz común)
├── shopify.py          -- JSON público
├── prestashop.py       -- HTML (tema IQIT)
├── woocommerce.py      -- Store API JSON + fallback HTML
├── odoo.py             -- HTML + JSON-LD por producto
├── opencart.py         -- HTML (tema por defecto)
└── generic_jsonld.py   -- listado configurable + JSON-LD por producto (CMS a medida)
```

**Por qué está separado así:** `base_script.py` tiene todo lo que es *transversal* a cualquier plataforma (configuración, modelo de datos, HTTP, dispatcher, salida). `scrapers/` tiene un archivo por plataforma, cada uno con la lógica específica de cómo esa plataforma expone su catálogo. Un scraper nuevo solo necesita implementar `scrape() -> list[Product]`.

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

Añade una entrada a `STORES` en `base_script.py` con el campo correspondiente a su plataforma. `StoreConfig.__post_init__` falla con un mensaje claro si falta algo imprescindible — esto es intencional, es la corrección estructural de un bug real que tuvo este proyecto (una tienda WooCommerce sin categoría bien acotada acabó trayéndose todo el catálogo de la tienda, no solo One Piece).

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
- **robots.txt / Crawl-delay** (`get_robots_rules`, `scrape_store`): antes de scrapear se comprueba (y cachea una semana, en `store_state.json`) el robots.txt de la tienda contra la URL de listado que se va a pedir. Si el `Disallow` la cubre, la tienda se excluye ese ciclo con motivo explícito en logs — no se ignora robots.txt para scrapear igualmente. Si declara `Crawl-delay`, se usa como mínimo entre peticiones a esa tienda (nunca por debajo de `DEFAULT_DELAY`).
- **Backoff persistido entre ejecuciones** (`store_state.py`, `_record_backoff_outcome`): a diferencia del circuit breaker de `query_store()` (en memoria, solo dentro del proceso), este cuenta fallos seguidos de `run_all_stores()` en **ejecuciones distintas** (persistido en `store_state.json`). Al tercer fallo seguido, la tienda queda en backoff (`STORE_BACKOFF_DEFAULT_SECONDS`, 30 min) y el próximo `run_all_stores()` la salta sin intentarla hasta que pase. Una exclusión por robots.txt no cuenta como fallo.

`store_state.json` es estado runtime local (no se versiona, ver `.gitignore`) — se borra sin problema, simplemente se vuelve a poblar en la siguiente ejecución. Es la pieza que, en el bloque B de `cambios-necesarios-scraper.md`, se sustituirá por lecturas/escrituras directas a la tabla `store` de Postgres.

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

1. **`sync_stores()`** — `STORES` (código) → tabla `store`, `UPSERT` por `website_url`. Solo toca los campos ESTÁTICOS que existen en `StoreConfig` (`name`, `platform`); los dinámicos (`crawl_delay_seconds`, `backoff_until`, `last_scraped_at`, `robots_checked_at`) viven solo en la BBDD y esta sincronización nunca los pisa.
2. **`persist_scrape_results()`** — los `Product` ya scrapeados → `store_product` + `price_history` + `restock_event`, en **una transacción por tienda** (no por producto, ni una única transacción gigante para las 56): un lote corrupto de una tienda hace `ROLLBACK` solo, sin perder las demás. Cada lote:
   - Lee el `stock_status` **anterior** de `store_product` antes de sobrescribirlo, para detectar la transición `agotado → disponible` (restock). Un `store_url` sin fila previa nunca cuenta como restock (alta nueva, no restock), y tampoco cuenta si no hay `product_id` confirmado todavía (bloque C, matching, aún no implementado).
   - Trunca defensivamente `raw_name`/`raw_variant`/`store_sku` a los límites del esquema si el HTML de una tienda concreta cuela texto anómalo (visto en Arte9, mismo tema Madara de la limitación de más abajo) — con aviso en logs, en vez de que ese único producto tire abajo la transacción de toda la tienda.
   - Normaliza `stock_status` a los valores exactos del enum (minúscula) — el scraper interno sigue en mayúsculas, la traducción vive solo aquí.

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
├── requirements.txt
├── base_script.py       -- configuración, modelo de datos, HTTP, dispatcher, main()
├── store_state.py       -- estado runtime por tienda entre ejecuciones (robots.txt, backoff) -- ver .gitignore
├── persistence.py       -- escritura a PostgreSQL (store/store_product/price_history/restock_event)
└── scrapers/
    ├── __init__.py
    ├── base.py
    ├── shopify.py
    ├── prestashop.py
    ├── woocommerce.py
    ├── odoo.py
    ├── opencart.py
    └── generic_jsonld.py
```

# Modelo de datos — App de stock unificado TCG

## Entidades y por qué existen cada una

### `game`
Solo Pokémon y One Piece por ahora, pero como tabla en vez de enum porque el diseño funcional ya contempla expandir a otros TCG en el futuro (Nivel 5). Coste de mantenerlo como tabla: cero. Coste de no hacerlo si luego expandes: migración.

### `category`
Tipo de producto (Booster Box, Booster Pack, Starter Deck, Illustration Box, Carta suelta...), como entidad propia en vez de enum fijo en `product`. Ventaja: puedes añadir categorías nuevas sin tocar código ni migrar el enum.

La hice **jerárquica** (`parent_category_id` nullable, auto-referenciada) para poder agrupar en dos niveles — por ejemplo "Sellado" como padre de "Booster Box", "Starter Deck", "Illustration Box", y "Singles" como padre de "Carta suelta". Esto da filtros de dos niveles en la web sin tabla extra. Si prefieres category plana (sin jerarquía), se quita el `parent_category_id` y ya está.

### `product` (producto canónico)
El corazón del sistema. Es el "OP-16 Booster Box EN" abstracto, independiente de qué tienda lo vende. Aquí es donde se agrupa todo para poder comparar precio entre tiendas.

Campos clave:
- `category_id` — FK a `category` (Booster Box, Starter Deck...)
- `set_code` — para poder filtrar por set (OP-16, SV-10...)
- `language` — EN/JP/ES (relevante en One Piece, donde una misma caja existe en varios idiomas y el precio varía mucho)
- `is_hot` / `hot_until` — controlan la frecuencia de scraping (ver punto 4 de "Decisiones tomadas")

### `store`
Cada una de las 53 tiendas. `platform` guarda qué scraper usar (woocommerce/prestashop/shopify/odoo/opencart/generic_jsonld/custom) — esto ya lo tienes de facto en tu `scraper_unificado.py`, aquí se formaliza como dato.

### `store_product` (listado)
El producto tal cual aparece en una tienda concreta: su URL, su nombre en crudo tal como lo escribió la tienda, su precio y stock *actuales*. Es la tabla que el scraper toca en cada pasada.

`product_id` es nullable y solo se rellena cuando el matching está **confirmado** — ver la cola de revisión en "Decisiones tomadas" más abajo.

### `price_history`
Una fila por `store_product` y **día** (`scraped_date`), con el precio y stock de ese día. Es lo que alimenta la curva de precio y el ranking de mejores ofertas. Alineado con un scraper que corre una vez al día.

### `restock_subscription`
Un dispositivo (identificado por su token de Web Push, sin login) siguiendo un producto canónico concreto. `store_id` es nullable: en v1 siempre `NULL` (avisa si vuelve a stock en cualquier tienda), preparado para admitir "solo en la tienda X" en el futuro sin migrar el esquema. `push_endpoint` y `push_keys_json` son los datos que exige la Web Push API para poder enviar la notificación más adelante.

### `restock_event`
Registro de cada vez que un `store_product` pasa de agotado a disponible y dispara notificaciones. Sirve para depurar ("¿por qué no me llegó el aviso de tal restock?") y para tener métricas (cuántos restocks se detectan al mes, cuántos suscriptores se avisan de media).

---

## Decisiones tomadas

### 1. Matching de `store_product` → `product`: híbrido (opción C)
Auto-matching por reglas (set_code + category + language extraídos del nombre en crudo) con umbral de confianza; lo que no alcanza el umbral cae en cola de revisión manual.

**Carga inicial del catálogo canónico**: Pokémon y One Piece TCG lanzan producto en lotes finitos y conocidos por set (cada set trae un puñado de productos: Booster Box, Booster Pack, 1-2 Starter Decks, a veces Illustration Box). No hace falta descubrir el catálogo raspando tiendas — se siembra manualmente (o semi-automáticamente vía la API pública de Pokémon TCG para ese lado) cada vez que sale un set nuevo, unas pocas veces al año. Así el catálogo canónico va siempre un paso por delante del scraping y el matching automático tiene contra qué comparar desde el primer día.

**Cola de revisión**: no es una tabla aparte — es simplemente el conjunto de `store_product` con `match_status != 'confirmed'`. Campos añadidos a `store_product`:
- `match_status` (unmatched / needs_review / confirmed)
- `match_confidence` (score del matcher automático, nullable)
- `suggested_product_id` (la sugerencia del matcher, pendiente de confirmar) — se mantiene separado de `product_id` para que un `product_id` poblado siempre signifique "confirmado", nunca "sugerido sin revisar"

**Corregido (2026-08-27, `docs/scraper/cambios-necesarios.md`):** `suggested_product_id` se retiró del esquema — `GET /matches` (antes `/matches/pending`) devuelve un top-3 de candidatos con su score cada uno, calculado en caliente (`ORDER BY similarity(name_canonical, raw_name) DESC LIMIT 3`), en vez de guardar una única sugerencia que quedaría obsoleta en cuanto se siembre un canónico nuevo más parecido. `match_confidence` se conserva, pero pasa a significar "score del match ya confirmado", no el de una sugerencia pendiente.

### 2. Alcance del restock: cualquier tienda en v1, tienda concreta preparado para el futuro
`restock_subscription.store_id` es nullable desde el principio: `NULL` = "avísame si vuelve a stock en cualquier tienda" (comportamiento único de v1). Queda el campo listo para cuando quieras ofrecer "avísame solo si vuelve en la tienda X", sin tener que migrar el esquema más adelante.

### 3. Granularidad de `price_history`: curva diaria
Una fila por `store_product` y día (`scraped_date`, no timestamp), alimentada por un scraper que corre una vez al día. Esto da directamente la curva de precio que buscas y simplifica el scraper (no hace falta comparar contra el último estado para decidir si insertar o no — siempre se inserta la foto del día).

**Implicación original**: con escaneo diario para todo, la detección de restock ocurriría como máximo una vez al día. Resuelto abajo en el punto 4 con frecuencia adaptativa para productos calientes.

### 4. Frecuencia de scraping: diaria + refresco individual para productos calientes

El scraper opera a nivel de **página de categoría por tienda** (una petición trae todos los productos de esa categoría de golpe), no a nivel de producto individual — por eso "escanear cada 2h todo" multiplicaría por 24x el tráfico hacia tiendas pequeñas, varias de las cuales ya hemos visto con protección anti-bot activa durante esta sesión.

La solución aprovecha que cada `store_product` guarda su propia `store_url` (la ficha individual del producto en esa tienda). Esto permite dos modos de scraping distintos:

- **Escaneo completo por categoría** (diario, todas las tiendas) — el que ya existe. Descubre productos nuevos y alimenta el matching.
- **Refresco individual** (cada 2-4h, solo `store_product` cuyo `product.is_hot = true`) — pide directamente la ficha individual de ese producto en esa tienda, sin tocar la categoría entera. Como solo afecta a un subconjunto pequeño de productos, el volumen extra es controlado, no 24x sobre todo el catálogo.

Campos añadidos a `product`:
- `is_hot` (bool) — activa el refresco frecuente para todos sus `store_product` asociados
- `hot_until` (fecha, nullable) — para que lo "caliente" expire automáticamente unas semanas después del lanzamiento del set, sin mantenimiento manual constante. Se puede marcar `is_hot=true` automáticamente al dar de alta producto de un set recién anunciado, con `hot_until` = fecha de lanzamiento + N semanas.

**Limitación a asumir**: el refresco individual solo vigila productos que la tienda **ya tiene listados** (existe la fila `store_product`). Si una tienda que antes no vendía ese producto empieza a venderlo de repente, eso solo se descubre en el siguiente escaneo diario por categoría — el refresco frecuente no descubre listados nuevos, solo vigila los conocidos con más frecuencia.

### 5. Descubrimiento temprano de productos nuevos (antes del barrido diario)

Separar **descubrir** que hay algo nuevo (barato, puede ser frecuente) de **extraer** los datos completos de ese producto (caro, es lo que hace el barrido diario) — no hace falta que sea la misma operación.

- **Polling de `sitemap.xml`** (mecanismo principal): WooCommerce, PrestaShop y Shopify generan automáticamente un sitemap con las URLs de producto. Comprobarlo cada 1-2h es mucho más barato que repetir el escaneo de categoría completo, porque solo hay que comparar URLs contra las ya conocidas en `store_product.store_url` — no hace falta parsear HTML. Cuando aparece una URL nueva, se dispara una extracción puntual solo de esa ficha.
- **Página de "novedades" propia** (oportunista, no universal): Shopify trae `/collections/new` de serie; algunas WooCommerce tienen equivalente. Se usa donde exista, no es el mecanismo principal porque no todas las plataformas lo ofrecen.
- **Calendario de lanzamientos de sets** (operativo, no una tabla): las fechas de salida se conocen con semanas de antelación. Alrededor de esas fechas se sube temporalmente la frecuencia del barrido completo de categoría (no solo el sitemap), porque es cuando de verdad aparece producto nuevo. Se gestiona como configuración/cron, no como entidad — se puede formalizar como tabla `set_release` más adelante si conviene automatizarlo del todo.

Campos añadidos a `store`:
- `sitemap_url` (nullable) — si la tienda no expone uno, simplemente no participa en el polling temprano y se limita al barrido diario
- `last_sitemap_checked_at` — para saber cuándo tocó la última comprobación

## Resumen de barridos de scraping

Cuatro mecanismos distintos, cada uno con un propósito y una frecuencia diferente. No son alternativos entre sí — funcionan en capas, cada uno resolviendo un problema que el anterior no cubre.

| Barrido | Frecuencia | Qué hace | Sobre qué actúa | Alimenta |
|---|---|---|---|---|
| **Barrido completo por categoría** | Diario | Recorre la página de categoría de cada tienda y extrae todos los productos de golpe (nombre, precio, stock) | Todas las tiendas activas | `store_product` (altas y actualizaciones), `price_history` (1 fila/día), cola de matching |
| **Polling de sitemap** | Cada 1-2h | Compara las URLs del `sitemap.xml` de la tienda contra las ya conocidas en `store_product.store_url`; si hay una nueva, dispara una extracción puntual solo de esa ficha | Tiendas con `sitemap_url` configurado | Altas tempranas en `store_product` (antes de que le toque al barrido diario) |
| **Refresco individual (producto caliente)** | Cada 2-4h | Pide directamente la ficha individual (`store_url`) de un `store_product` concreto, sin tocar la categoría entera | `store_product` cuyo `product.is_hot = true` | Actualización rápida de `stock_status` → detección de restock casi en tiempo real |
| **Escaneo reforzado por lanzamiento** | Temporal (unos días antes/después de la fecha de salida de un set) | Sube la frecuencia del *barrido completo por categoría* a varias veces al día, no solo 1x — es el mismo mecanismo, con más frecuencia durante la ventana | Todas las tiendas, acotado a la ventana de lanzamiento | Descubre producto nuevo real cuanto antes, cuando más probable es que aparezca |

**Cómo se relacionan**: el barrido diario es la base que nunca falta. El sitemap y el escaneo reforzado son formas de **descubrir antes** lo que el barrido diario descubriría igualmente, pero tarde. El refresco individual es distinto: no descubre nada nuevo, solo **vigila con más frecuencia** algo que ya se conoce y que importa especialmente (producto caliente).

---

## Estándares y buenas prácticas de scraping incorporadas

Separados en dos grupos: los que necesitan guardar estado nuevo (afectan al esquema) y los que son puramente política de comportamiento del scraper (no tocan el modelo de datos).

### Con impacto en el esquema

| Estándar | Qué resuelve | Campo(s) nuevos |
|---|---|---|
| **`<lastmod>` del sitemap** (protocolo sitemaps.org) | Filtrar directamente "todo lo modificado desde mi última comprobación", en vez de comparar listas completas de URLs. Más preciso (detecta también cambios de precio/stock en producto ya conocido, no solo altas) | Ninguno nuevo — usa `store.last_sitemap_checked_at` ya existente como corte |
| **WooCommerce Store API** (`/wp-json/wc/store/v1/products`) | JSON estructurado en vez de parsear HTML — más robusto (no se rompe con cambios de theme) y más ligero. Aplica a la mayoría de tus tiendas WooCommerce | `store.has_structured_api` (bool), `store.api_endpoint` (nullable) |
| **Peticiones condicionales HTTP** (`If-Modified-Since` / `ETag`) | Polling de sitemap y refresco de productos calientes sin descargar nada si no ha cambiado (`304 Not Modified`) | `store_product.last_etag`, `store_product.last_modified_header` (ambos nullable) |
| **`robots.txt` / `Crawl-delay`** | Respetar el ritmo que la propia tienda pide explícitamente, reduce bloqueos | `store.crawl_delay_seconds` (nullable, parseado de robots.txt) |
| **`429 Too Many Requests` + `Retry-After`** | Backoff automático cuando el servidor lo pide explícitamente, en vez de seguir insistiendo | `store.backoff_until` (nullable datetime) — mientras esté en el futuro, el scraper salta esa tienda |

### Puramente política del scraper (sin campo nuevo)

- **Datos estructurados `schema.org/Product` (JSON-LD)** — ya usado para el bug de stock de TCG Legacy (Odoo); se extiende como método de extracción preferente en WooCommerce/PrestaShop/Shopify también, antes de recurrir a selectores CSS frágiles.
- **User-Agent identificable** — el scraper se identifica como lo que es, con forma de contacto, en vez de simular un navegador.

---

## Siguiente paso natural
Con esto ya se puede pasar a arquitectura: qué motor de base de datos (sigues con SQLite o conviene Postgres dado que ahora hay escritura desde el scraper y lectura desde una API web a la vez), cómo se estructura el backend que sirve la API, y cómo encaja el envío de push notifications como proceso.

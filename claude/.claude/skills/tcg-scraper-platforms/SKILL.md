---
name: tcg-scraper-platforms
description: Cómo identificar la plataforma de e-commerce de una tienda TCG española y añadirla a scraper_unificado.py (el scraper multi-tienda de comparación de precios de One Piece TCG). Úsala siempre que el usuario pase una URL nueva de tienda para "añadir al script", pregunte "en qué plataforma corre esta tienda", reporte que una tienda scrapeada muestra datos incorrectos (categoría equivocada, stock mal detectado, productos de más), o pida crear soporte para una plataforma nueva (un scraper que no sea Shopify/PrestaShop/WooCommerce/Odoo/OpenCart). Contiene las firmas de URL para identificar cada plataforma sin necesidad de adivinar, y una lista de errores ya cometidos (y sus arreglos) para no repetirlos.
---

# Añadir tiendas a scraper_unificado.py

Este documento condensa lo aprendido construyendo y ampliando el scraper multi-tienda: cómo identificar la plataforma de una tienda nueva, cómo configurarla correctamente, y una lista de errores reales ya cometidos para no repetirlos.

## Arquitectura del script (recordatorio rápido)

- `StoreConfig` — un dataclass por tienda. **Se autovalida en `__post_init__`**: si falta un campo obligatorio para su plataforma, lanza `ValueError` al arrancar el script en vez de scrapear mal en silencio. Cualquier plataforma nueva debe seguir este mismo patrón.
- `Product` — fila normalizada común a todas las plataformas (incluye `variant`, pensado desde el diseño para productos con variantes de idioma/grading).
- `BaseStoreScraper` — clase abstracta con `scrape() -> list[Product]`. Cada plataforma es una subclase.
- `SCRAPER_CLASSES: dict[Platform, type[BaseStoreScraper]]` — el dispatcher. Añadir una plataforma nueva = añadir su entrada aquí.
- `request_with_retries()` — reintentos con backoff para TODAS las peticiones HTTP. Cualquier scraper nuevo debe usarlo, nunca `session.get()` a pelo.
- `StoreLogger` — une `print()` + marcar actividad (para el timeout por inactividad) en una llamada.

## Proceso para añadir una tienda nueva

### 1. Identificar la plataforma — por firma de URL primero, sin gastar fetches

Antes de hacer ningún `web_fetch`, mira el patrón de la URL. Estas firmas identifican la plataforma con alta confianza:

| Plataforma | Firma de URL | Ejemplo |
|---|---|---|
| **Shopify** | `/collections/<slug>`, parámetros `filter.v.*` | `tienda.com/collections/one-piece?filter.v.availability=1` |
| **PrestaShop** | `/<id-numérico>-<slug>` o `/categoria/<slug>-<id>` | `tienda.com/47-one-piece-card-game` |
| **WooCommerce** | `/categoria-producto/...`, `/product-category/...`, `?add-to-cart=<id>`, `stock_status=instock`, `orderby=price-desc` | `tienda.com/categoria-producto/tcg/one-piece/` |
| **Odoo** | `/shop/category/<slug>-<id>` | `tienda.com/shop/category/one-piece-10` |
| **OpenCart** | `index.php?route=product/category&path=<id>` o `<id_padre>_<id_hijo>` | `tienda.com/index.php?route=product/category&path=77_96` |

Si la URL no encaja con ninguna, **no adivines**: haz un `web_fetch` o `web_search` y mira las meta-etiquetas (`meta-generator`) y patrones del HTML (rutas `/wp-content/`, `csrf_token` con `odoo`, etc.). Plataformas ya identificadas pero SIN soporte todavía en el script: OpenCart *(ya soportado)*, Hostinger Website Builder, y sistemas propietarios con parámetros tipo `idsite=`/`criterio_id=` sin patrón reconocible — para estas últimas, no fuerces una identificación a ciegas; repórtalo como pendiente.

### 2. Verifica con un fetch real antes de escribir ni una línea de config

**Nunca configures `category_slug`/`collection`/`category_url` solo por lo que dice el nombre del enlace del menú.** Haz el fetch y comprueba:

- ¿Es una categoría **específica de One Piece**, o una categoría **mixta** (todos los TCG juntos: Pokémon, Magic, Riftbound, Lorcana...)? Esto decide si necesitas `category_slug` solo, o combinarlo con `name_must_include`.
- ¿Cuántos productos tiene la categoría? Si el número es sospechosamente alto (cientos) para lo que debería ser solo One Piece, es la categoría equivocada — busca una más específica antes de configurar nada.
- Copia el **slug exacto** de la URL, no lo traduzcas ni normalices a mano.

### 3. Decide el scoping (WooCommerce en particular)

`StoreConfig` para WooCommerce tiene tres mecanismos, pensados para combinarse:

- `woocommerce_category_slug` — el método preferido. Se manda como filtro de la Store API (`?category=slug`) y ADEMÁS se usa como post-filtro local comparando por **slug exacto** (nunca por nombre de categoría — ver más abajo por qué).
- `woocommerce_name_must_include` — fallback cuando no hay categoría estándar fiable (taxonomía custom de Elementor, filtro por atributo en vez de categoría...). Exige que el nombre del producto contenga ciertas palabras.
- `woocommerce_fallback_paths` — rutas para el scraping HTML si la Store API no está disponible.

**Estos dos primeros se combinan con AND, no con OR.** Si configuras ambos, el producto debe cumplir los dos a la vez. Esto importa cuando una tienda tiene una categoría mixta de TCG (`category_slug="jc-tcg"`) Y necesitas además filtrar por nombre (`name_must_include=("one piece",)`) para quedarte solo con One Piece dentro de esa categoría — con OR se colarían los otros 200+ productos de la categoría entera.

La validación de `StoreConfig` exige al menos un mecanismo de *scoping* (`category_slug` o `name_must_include`) Y al menos un mecanismo de *fetch* (`category_slug` o `fallback_paths`). Si falta alguno, el script no arranca — es intencional.

### 4. Escribe la config, compílala, y corre tests con datos simulados

Nunca confíes en que el scraper funciona solo porque compila. Antes de darlo por bueno:

```python
python3 -m py_compile scraper_unificado.py && python3 -m pyflakes scraper_unificado.py
```

Y escribe un test con `unittest.mock` que simule la respuesta HTTP con datos representativos de ESA tienda (nombre de producto real, HTML de categoría real si lo tienes) y verifique: cuántos productos pasan el filtro, que el precio se parsea bien, y sobre todo — el stock.

### 5. El stock es lo más fácil de acertar mal. Sé conservador.

Esta es la lección más repetida en el desarrollo de este script. Antes de asumir cómo se marca "agotado" en una plataforma nueva:

- **No adivines un selector CSS de ribbon/badge sin ver HTML real.** En Odoo, un ribbon con texto "Disponible en tienda" resultó ser algo totalmente ajeno al stock online (recogida en tienda física), y una clase CSS supuesta (`o_wsale_soldout_badge`) simplemente no existía.
- **Muchas plataformas calculan el stock por JavaScript en el cliente**, no en el HTML que llega por `requests`/`BeautifulSoup`. Si no hay ninguna señal textual de stock en el HTML crudo, es señal de que hay que buscar en otro sitio (ver JSON-LD abajo) o rendirse a marcar el estado como desconocido.
- **Busca primero bloques `<script type="application/ld+json">` con `"@type": "Product"`.** El campo `offers.availability` (`https://schema.org/InStock` / `OutOfStock`) es una señal estructurada, estable, y generada automáticamente por el motor de la tienda para SEO — mucho más fiable que perseguir clases CSS de tema en tema. Esto fue lo que arregló Odoo de verdad.
- **Si no hay ninguna señal fiable de stock (ni JSON-LD, ni clase CSS, ni texto), no asumas "DISPONIBLE" por defecto.** Marca el estado como `"DESCONOCIDO"` explícitamente (ver `OpenCartScraper` para el patrón) y dilo en el log. Un dato ausente es mejor que un dato falso — sobre todo en un comparador de precios donde alguien puede intentar comprar algo agotado.

### 6. Pide HTML real en cuanto algo no cuadre — no seguir adivinando

Si tras una primera pasada un selector no funciona (0 productos, stock siempre igual, categoría contaminada), el patrón correcto es:

1. Reconocer explícitamente que la suposición puede estar mal.
2. Pedir al usuario el HTML real de la página problemática (código fuente, Ctrl+U) en vez de proponer un tercer selector a ciegas.
3. Arreglar con datos reales, y añadir un test que reproduzca exactamente ese caso para que no vuelva a romperse.

Adivinar dos veces seguidas sin datos reales casi siempre sale mal — mejor parar y pedir el HTML.

## Errores ya cometidos (y su arreglo) — no los repitas

- **Comparar categorías por nombre en vez de por slug.** En Arte9, la categoría de TCG y la de merchandising (tazas, llaveros) se llamaban ambas "One Piece" por *nombre*, pero tenían slugs distintos (`one-piece` vs `onepiecemercha`). Comparar por nombre coló merchandising en el catálogo de TCG. **Siempre comparar por slug exacto.**
- **Filtrar por categoría/nombre con lógica OR cuando hace falta AND.** Ver sección 3 arriba.
- **Asumir que todas las variantes de un producto Shopify comparten stock.** Un mismo producto puede tener una variante en inglés agotada y otra en japonés disponible — cada variante necesita su propia fila con su propio `stock_status`, no una fila por producto.
- **Construir la URL de la siguiente página a mano.** Distintas tiendas WooCommerce paginan distinto (`/page/N/` vs `?product-page=N` vs otros esquemas de tema/plugin). Hay que seguir el `href` real del enlace "siguiente" del propio HTML, nunca construirlo por fórmula.
- **No filtrar por categoría en absoluto en la Store API de WooCommerce.** Sin `category_slug`, la API se trae el catálogo ENTERO de la tienda (cómics, manga, juegos de mesa...), lento y con falsos positivos/negativos en la clasificación.
- **Asumir que un ribbon/badge de texto indica stock.** Ver sección 5.
- **Asumir "disponible" quan no hay señal de stock.** Ver sección 5.

## Checklist para añadir una tienda nueva

1. [ ] Identificar plataforma por firma de URL (tabla arriba); si no encaja, fetch/search para confirmar por `meta-generator` o patrones de HTML.
2. [ ] Fetch real de la página de categoría propuesta — confirmar que es específica de One Piece, no mixta.
3. [ ] Si es WooCommerce con categoría mixta: decidir `category_slug` + `name_must_include` combinados, o solo uno de los dos.
4. [ ] Escribir el `StoreConfig`, con comentario explicando cualquier peculiaridad (categorías separadas por idioma, typos reales en la URL, etc.).
5. [ ] `py_compile` + `pyflakes` limpios.
6. [ ] Test con `unittest.mock` simulando datos representativos de esa tienda — al menos: filtrado correcto, precio, y stock.
7. [ ] Si la plataforma es nueva (no Shopify/PrestaShop/WooCommerce/Odoo/OpenCart): escribir una subclase de `BaseStoreScraper`, registrarla en `SCRAPER_CLASSES`, y documentar en su docstring qué se pudo verificar contra HTML real y qué quedó sin verificar.
8. [ ] Si algo no se pudo verificar con HTML real, decirlo explícitamente en un comentario/docstring — no presentar una suposición como un hecho confirmado.

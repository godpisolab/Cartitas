# Tests — Store Monitor

523 tests (`pytest`), pirámide invertida a propósito respecto a un proyecto típico: el riesgo real de este proyecto no es "se rompió la lógica de negocio pura" (barata de cubrir con unitarios), sino "una tienda cambió su HTML" o "el SQL no hace lo que creo" — de ahí el peso en integración con HTTP mockeado y Postgres real, no solo mocks de todo.

```
        /  E2E (2)  \           main() completo, con y sin Postgres disponible
       / Integración (~70) \    scrapers contra HTTP mockeado + Postgres real
      /  Unitarios (~220)    \  clasificación, parseo, validación, dispatcher
```

## Cómo correr los tests

```bash
pip install -r requirements-dev.txt
```

Los tests que **no** piden la fixture `db_conn` no tocan Postgres en absoluto — son la mayoría (~200 de 269) y corren en menos de un segundo, sin Docker. Los que sí la piden (persistencia, `store_state`, matching, bloque E, E2E) necesitan una base de datos de test **separada** de la de desarrollo, en el mismo contenedor de `docker-compose.yml`:

```bash
docker exec cartitas-postgres psql -U cartitas -d cartitas -c "CREATE DATABASE cartitas_test;"
docker exec -i cartitas-postgres psql -U cartitas -d cartitas_test < ../schema-postgresql-app-tcg.sql
```

Si `cartitas_test` no está accesible, esos tests se **saltan solos** (`pytest.skip`, no fallan) — no hace falta levantar Docker para trabajar en la lógica de clasificación/scrapers/dispatcher.

```bash
pytest                                        # todo
pytest tests/test_classify_product.py -v      # un fichero
pytest -k "restock"                           # por nombre
pytest --cov=. --cov-report=term-missing \
       --cov-config=<(printf '[run]\nomit = tests/*,conftest.py')   # con cobertura
```

Cada test que usa `db_conn` parte de tablas **vacías** (`TRUNCATE` automático antes de cada test, ver `conftest.py`) — no una transacción con rollback, porque `persistence.py` hace sus propios `commit()` internos y un rollback exterior no deshace nada de eso.

## Qué cubre cada fichero

| Fichero | Módulo bajo test | Qué prueba |
|---|---|---|
| `test_classify_product.py` | `classify.classify_product`, `_detect_language` | **Recognition Pipeline (2026-08-30, `docs/propuestas/guia_nuevo_matcher.md`):** tabla de clasificación completa contra la taxonomía nueva (`ONE_PIECE`/`EXTRA_BOOSTER`/`PREMIUM_BOOSTER_BOX`/`PREMIUM_CARD_COLLECTION`/`STARTER_DECK`/`SLEEVES`/etc., con el campo `packaging` en vez de categorías separadas de caja/sobre/case), el catch-all genérico a `ONE_PIECE` sin código ni título, las dos regresiones encontradas al validar contra la suite real (código de família con `(?!-\d)` para no confundir numeración de carta individual; guarda de rango `_RANGO_CODIGOS_RE` en las funciones de extracción de Fase 2), `main_set` derivado solo para `ONE_PIECE`/`DOUBLE_PACK` (antes de un "OP\d+" literal en cualquier nombre), y las família de Fase 0 (`PROMO_CARD`/`MYSTERY_PACK`/`DICE_ACCESSORY`, ahora `not_applicable` siempre) sin categoría. Además: la regresión de `" en "` como preposición española, las 5 ramas de idioma (EN/JP/ES/KR + fallback a `variant_title`), lista blanca de prefijos reales (`OP/ST/DP/EB/PRB/DF`, evita falsos positivos como "VOL2"), extracción por volumen para Illustration Box/Playmat, `_normalize_for_lookup` (decodifica entidades HTML), y las tablas de lookup personaje/título de release por família exclusiva (Corrección 4: nunca compartidas entre OP/PRB/EB, a diferencia del `_RELEASE_TITLE_CODES` único que tenía el sistema anterior). |
| `test_parse_price.py` | `classify.parse_price_text`, `parse_price_minor_unit` | Formato español/anglosajón, símbolo antes/después, tipos numéricos directos, entradas vacías/inválidas sin excepción |
| `test_store_config.py` | `domain.StoreConfig.__post_init__` | Validación de configuración por plataforma — la corrección estructural del bug real de Arte9/ZIAL (WooCommerce sin scoping) |
| `test_request_with_retries.py` | `http_client.request_with_retries` y compañía | Reintentos (200/500/timeout/404/304), `Retry-After` (segundos, fecha HTTP, exceso), reto anti-bot de cookie JS, fallback SSL — HTTP y `time.sleep` mockeados, cero esperas reales |
| `test_robots_check_target.py` | `dispatcher._robots_check_target` | Mapeo de URL a comprobar contra robots.txt, una por cada una de las 6 plataformas |
| `test_dispatcher.py` | `dispatcher.get_robots_rules`, `query_store`, `run_all_stores` | Caché de robots.txt (TTL), circuit breaker (apertura/cierre/exclusiones), backoff persistido entre ejecuciones (A.3) — `store_state` sustituida por un dict en memoria para aislar la lógica del dispatcher de Postgres |
| `test_scraper_shopify.py` | `scrapers/shopify.py` | Paginación, variantes con stock mixto, `refresh_product` (E.2), y (2026-08-27) el campo `tags` del comerciante se pasa como `type_hint`/`Product.tags`, normalizado a string siempre -- 12 de 53 tiendas reales devuelven `tags` como lista JSON en vez de cadena separada por comas, y sin normalizar petaba con `AttributeError` (esas tiendas se perdían enteras) |
| `test_scraper_woocommerce.py` | `scrapers/woocommerce.py` | Scoping AND (no OR) de categoría+nombre, fallback API→HTML, paginación de la Store API, `_clean_leaked_markup`/`_extract_price` (casos reales de Arte9), `refresh_product`, y (2026-08-27) `_looks_truncated`/`_resolve_truncated_name` — resuelve nombres recortados en el listado visitando la ficha individual, genérico por patrón, no atado a ninguna tienda |
| `test_scraper_prestashop.py` | `scrapers/prestashop.py` | Paginación, detección de página repetida, límite duro de 50 páginas, microdata schema.org en la ficha individual; y (2026-08-29) `_looks_truncated`/`_resolve_truncated_name` generalizado desde `WooCommerceScraper` (mismo patrón, ahora visto real en Distrito Zero con el tema IQIT) |
| `test_scraper_odoo.py` | `scrapers/odoo.py` | JSON-LD de disponibilidad (InStock/OutOfStock/ausente), paginación, `refresh_product` |
| `test_scraper_opencart.py` | `scrapers/opencart.py` | Palabras clave de stock en tarjeta vs. ficha completa (falso positivo real de "opciones disponibles"), extracción de `id_product`, saneo del prefijo "Clic para ampliar" |
| `test_scraper_generic_jsonld.py` | `scrapers/generic_jsonld.py` | `@type` mayúscula/minúscula, oferta anidada (`AggregateOffer`), fallback de disponibilidad (span → meta → desconocido) |
| `test_persistence.py` | `persistence.py` | `sync_stores` (UPSERT, campos dinámicos protegidos), las 5 reglas de restock (B.2), atomicidad por tienda (B.3), truncado defensivo, filtrado de productos inválidos — contra Postgres real |
| `test_refresh_hot_products.py` | `persistence.refresh_hot_products` | Selección de calientes (`is_hot`/`hot_until`), agrupación por URL, aplicar resultado del refresco (precio/stock/restock), errores del scraper |
| `test_store_state.py` | `store_state.py` | Lectura/escritura contra columnas de `store` (migrado de JSON), degradación sin Postgres disponible |
| `test_matcher.py` | `matcher.py` | **Evidence Builder / Decision Policy (2026-08-30):** `build_evidence()`/`decide()` como tabla de decisión pura separada de la consulta a BBDD -- coincidencia EXACTA de nombre (`exact_name_match`, confirma sin mirar nada más, nuevo en el rediseño), camino rápido por `set_code`+idioma+`packaging`+cantidad no ambigua (sin depender del score), categoría de único SKU, y el camino de siempre con los límites exactos de similitud (`>`/`>=` en 0.6 y 0.35). `_best_candidate` prioriza `set_code` exacto, luego idioma, luego `packaging` (columna `product.packaging`, sustituye a `is_box_variant()`/ILIKE de texto) sobre similitud pura; solo rechaza por "set distinto" cuando AMBOS lados traen código; busca en TODO el catálogo por `set_code` exacto si la categoría derivada no tiene candidato, marcando ese candidato como `es_fallback`. Exclusión `not_applicable` de las 5 família que nunca entran en el pipeline (`LOTE_CARTAS`/`OTROS`/`PROMO_CARD`/`MYSTERY_PACK`/`DICE_ACCESSORY` -- las tres últimas suben a Fase 0 en el rediseño). `run_matching` end-to-end, `find_missing_canonical_candidates` agrupando por `(tipo, set_code, idioma, packaging)` con fallback cross-categoría. |
| `test_matcher_casos_reales_cola.py` | `matcher.py` | Casos reales literales de la cola de revisión (export Cardzone/Pokemillon, 2026-08-28) contra un catálogo canónico EN/JP realista -- confirma que el fix de desempate por idioma mueve a `confirmed` las cajas/sobres sueltos que antes se quedaban en `needs_review` por azar del score, sin tocar `cantidad_es_ambigua` (bundles "Pack N Sobres" siguen en revisión) ni el gate de `es_fallback` (cartas promo sin canónico real) |
| `test_seed_official_catalog.py` | `seed_official_catalog.py` | Construcción de nombre canónico, duplicado sobre/caja, idempotencia, productos sin categoría omitidos. **Recognition Pipeline (2026-08-30):** sobre/caja/case se siembran ahora en la MISMA categoría de família (`one-piece`/`extra-booster`/`premium-booster-box`), distinguidos por `product.packaging` -- ya no hay categoría `booster-case` separada, el multiplicador de Case se lee de `_PACKAGING_UNITS` (`shared.classify`, fuente única compartida con `_detect_packaging`) en vez de una copia propia. `_JP_VARIANT_CATEGORY_SLUGS` cubre `one-piece`/`extra-booster`/`premium-booster-box`/`double-pack`/`starter-deck`; `premium-card-collection` (família nueva, separada de Premium Booster Box) queda fuera a propósito -- la demanda JP confirmada era específicamente de Premium Booster Box, no de Premium Card Collection. |
| `test_sitemap_poller.py` | `sitemap_poller.py` | Sitemap índice vs. plano, filtro por sub-sitemap "product", filtro por prefijo de ruta conocido, filtro por palabra clave del juego, tope `MAX_NEW_URLS_PER_POLL` |
| `test_restock_notifier.py` | `restock_notifier.py` | Envío exitoso, `410 Gone` (borra suscripción), error recuperable (no borra), sin VAPID configurada, aislamiento entre tiendas (`store_id`) |
| `test_scheduler.py` | `scheduler.py` | Los 3 jobs quedan registrados con el tipo de trigger correcto, cada job orquesta las llamadas esperadas y cierra la conexión aunque falle |
| `test_main_e2e.py` | `base_script.main` | Único E2E: pipeline completo (scrape → CSV → Postgres) con 2 tiendas mockeadas, y con Postgres inaccesible (el CSV se genera igual) |

## Bugs reales encontrados escribiendo estos tests

No son casos felices — cada uno de estos se encontró porque un test falló de forma inesperada, no porque se buscara a propósito:

1. **Crítico — pérdida de datos silenciosa**: `UNIQUE(store_id, store_url, raw_variant)` en `store_product` nunca detectaba conflicto cuando `raw_variant` era `NULL` (SQL estándar: `NULL` nunca es igual a otro `NULL` a efectos de una restricción `UNIQUE`). Es el caso de **todas** las plataformas salvo Shopify — cada re-scrape de una tienda WooCommerce/PrestaShop/OpenCart/Odoo/JSON-LD habría creado una fila duplicada nueva en vez de actualizar la existente, sin límite, cada día. Corregido con `UNIQUE NULLS NOT DISTINCT` (Postgres 15+).
2. **Código inalcanzable**: la protección de "20 páginas sin scoping" en `WooCommerceScraper._paginate_api` nunca puede dispararse — `_product_in_scope` acepta todo incondicionalmente en el mismo caso (`has_scope=False`), así que `products` nunca puede quedarse vacío para que la condición de corte se cumpla. El primer intento de testear esto entró en un bucle infinito real (parado a mano), que es justo la prueba de que el hallazgo era cierto.
3. **Inconsistencia de diseño**: si el JSON-LD de `OdooScraper` existe pero no trae `offers.availability`, cae a `DISPONIBLE` por defecto — al contrario que el resto del proyecto (`OpenCartScraper`/`GenericJsonLdScraper` caen a `DESCONOCIDO` en el mismo caso). No se ha visto ninguna tienda Odoo real sin ese campo, pero queda documentado como riesgo latente.
4. **Enum incompleto**: `_detect_language()` puede devolver `"KR"` (coreano), pero el ENUM `product_language` del esquema solo admite `('EN', 'JP', 'ES')`. No revienta nada hoy (no hay ningún punto que compare ese valor contra la columna), pero un producto canónico coreano nunca podría matchear.

Ninguno de los cuatro se "arregló para que el test pasara" sin más — se corrigió el código real (los dos primeros) o se documentó explícitamente el comportamiento actual con una razón (los otros dos), según correspondiera.

**2026-08-29, auditoría manual de la cola `needs_review` (182 → 49 filas, ver `docs/matching/motor-matching.md`)** — a diferencia de los cuatro de arriba, estos no se encontraron porque un test fallara, sino inspeccionando fila a fila un CSV de auditoría exportado contra Postgres real; los tests de regresión se añadieron DESPUÉS, para que no vuelvan a colarse:

5. **Regex de `set_code` sin `re.IGNORECASE`**: a diferencia de `main_set_match`/`_DOUBLE_PACK_SET_CODE_RE` (que sí lo llevaban), el regex genérico de `set_code` en `classify_product()` era case-sensitive contra una lista de prefijos en mayúsculas -- cualquier tienda que escribiera el código en minúscula/mixto (`"Caja Op04"`, `"Eb-02"`) perdía el código por completo. Afectaba sistemáticamente a una tienda entera (Saruman Games).
6. **Bug de composición en el lookup nuevo**: las claves de `_STARTER_DECK_CHARACTER_CODES` con `"&"` literal (`"ace & newgate"`) nunca podían matchear contra texto normalizado, porque `_normalize_for_lookup()` convierte `"&"`/`"&amp;"`/`"&#038;"` en un espacio, no en la palabra "and" -- la clave tenía que ser la forma YA normalizada (`"ace newgate"`). Encontrado releyendo el propio CSV de auditoría tras el primer fix, no en el momento de escribirlo.
7. **`raw_tags` (metadato de catálogo reciclado entre productos sin relación) con el mismo peso que `name` en el bucle de `CLASSIFICATION_RULES`**: una tag reutilizada (`"PRB-02 The Best vol. 2"` en un Illustration Box; `"Cajas"` en un Devil Fruits Collection; `"Sobre One Piece, Sobres"` en un Double Pack con código `DP-NN` explícito en el propio `name`) podía ganarle al keyword correcto del `name` solo por venir antes en la lista (`CLASSIFICATION_RULES` es primer-match-gana, no el más relevante). `name+variant` se prueban ahora ANTES que las tags; estas solo entran como respaldo si `name+variant` solos se quedan en `OTROS` (mismo caso que motivó incluirlas en 2026-08-27, sin regresión).

## Qué queda sin cubrir (a propósito)

- Líneas de `print()`, el bloque `if __name__ == "__main__":` de cada módulo, y ramas defensivas de errores muy poco probables (p.ej. un `except` alrededor de una operación que en la práctica no puede fallar dado el código anterior) — no aportan nada probándolas.
- El E2E (`test_main_e2e.py`) es deliberadamente uno solo: la pirámide de este proyecto invierte el peso hacia integración/scrapers, no hacia E2E.
- No hay ningún test contra una tienda real por red — todo el HTTP está mockeado. La verificación contra tiendas reales se hizo a mano durante el desarrollo de cada scraper (ver los comentarios "verificado contra HTML real de..." en el propio código) y queda fuera del alcance de una suite automatizada rápida.

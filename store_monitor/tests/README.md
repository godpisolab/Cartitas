# Tests — Store Monitor

269 tests (`pytest`), pirámide invertida a propósito respecto a un proyecto típico: el riesgo real de este proyecto no es "se rompió la lógica de negocio pura" (barata de cubrir con unitarios), sino "una tienda cambió su HTML" o "el SQL no hace lo que creo" — de ahí el peso en integración con HTTP mockeado y Postgres real, no solo mocks de todo.

```
        /  E2E (2)  \           main() completo, con y sin Postgres disponible
       / Integración (~65) \    scrapers contra HTTP mockeado + Postgres real
      /  Unitarios (~200)    \  clasificación, parseo, validación, dispatcher
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
| `test_classify_product.py` | `base_script.classify_product`, `_detect_language` | Tabla de clasificación completa, la regresión de `" en "` como preposición española, el límite real de la regex de `main_set` (`OP-99` vs `OP-100`), y las 5 ramas de idioma (EN/JP/ES/KR + fallback a `variant_title`) |
| `test_parse_price.py` | `base_script.parse_price_text`, `parse_price_minor_unit` | Formato español/anglosajón, símbolo antes/después, tipos numéricos directos, entradas vacías/inválidas sin excepción |
| `test_store_config.py` | `base_script.StoreConfig.__post_init__` | Validación de configuración por plataforma — la corrección estructural del bug real de Arte9/ZIAL (WooCommerce sin scoping) |
| `test_request_with_retries.py` | `base_script.request_with_retries` y compañía | Reintentos (200/500/timeout/404/304), `Retry-After` (segundos, fecha HTTP, exceso), reto anti-bot de cookie JS, fallback SSL — HTTP y `time.sleep` mockeados, cero esperas reales |
| `test_robots_check_target.py` | `base_script._robots_check_target` | Mapeo de URL a comprobar contra robots.txt, una por cada una de las 6 plataformas |
| `test_dispatcher.py` | `base_script.get_robots_rules`, `query_store`, `run_all_stores` | Caché de robots.txt (TTL), circuit breaker (apertura/cierre/exclusiones), backoff persistido entre ejecuciones (A.3) — `store_state` sustituida por un dict en memoria para aislar la lógica del dispatcher de Postgres |
| `test_scraper_shopify.py` | `scrapers/shopify.py` | Paginación, variantes con stock mixto, `refresh_product` (E.2) |
| `test_scraper_woocommerce.py` | `scrapers/woocommerce.py` | Scoping AND (no OR) de categoría+nombre, fallback API→HTML, paginación de la Store API, `_clean_leaked_markup`/`_extract_price` (casos reales de Arte9), `refresh_product` |
| `test_scraper_prestashop.py` | `scrapers/prestashop.py` | Paginación, detección de página repetida, límite duro de 50 páginas, microdata schema.org en la ficha individual |
| `test_scraper_odoo.py` | `scrapers/odoo.py` | JSON-LD de disponibilidad (InStock/OutOfStock/ausente), paginación, `refresh_product` |
| `test_scraper_opencart.py` | `scrapers/opencart.py` | Palabras clave de stock en tarjeta vs. ficha completa (falso positivo real de "opciones disponibles"), extracción de `id_product`, saneo del prefijo "Clic para ampliar" |
| `test_scraper_generic_jsonld.py` | `scrapers/generic_jsonld.py` | `@type` mayúscula/minúscula, oferta anidada (`AggregateOffer`), fallback de disponibilidad (span → meta → desconocido) |
| `test_persistence.py` | `persistence.py` | `sync_stores` (UPSERT, campos dinámicos protegidos), las 5 reglas de restock (B.2), atomicidad por tienda (B.3), truncado defensivo, filtrado de productos inválidos — contra Postgres real |
| `test_refresh_hot_products.py` | `persistence.refresh_hot_products` | Selección de calientes (`is_hot`/`hot_until`), agrupación por URL, aplicar resultado del refresco (precio/stock/restock), errores del scraper |
| `test_store_state.py` | `store_state.py` | Lectura/escritura contra columnas de `store` (migrado de JSON), degradación sin Postgres disponible |
| `test_matcher.py` | `matcher.py` | Tabla de umbrales de C.2 con límites exactos (`>` vs `>=` en 0.6 y 0.35), exclusión `not_applicable` de LOTE_CARTAS/OTROS, `run_matching` end-to-end, `find_missing_canonical_candidates` (C.1) |
| `test_seed_official_catalog.py` | `seed_official_catalog.py` | Construcción de nombre canónico, duplicado Booster Box/Pack, idempotencia, productos sin categoría omitidos |
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

## Qué queda sin cubrir (a propósito)

- Líneas de `print()`, el bloque `if __name__ == "__main__":` de cada módulo, y ramas defensivas de errores muy poco probables (p.ej. un `except` alrededor de una operación que en la práctica no puede fallar dado el código anterior) — no aportan nada probándolas.
- El E2E (`test_main_e2e.py`) es deliberadamente uno solo: la pirámide de este proyecto invierte el peso hacia integración/scrapers, no hacia E2E.
- No hay ningún test contra una tienda real por red — todo el HTTP está mockeado. La verificación contra tiendas reales se hizo a mano durante el desarrollo de cada scraper (ver los comentarios "verificado contra HTML real de..." en el propio código) y queda fuera del alcance de una suite automatizada rápida.

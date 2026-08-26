# Cartitas API

Servicio FastAPI + SQLModel, hermano de `store_monitor/` (no un módulo dentro de él -- ver `docs/estandares-implementacion-api.md`, sección 1). Sirve el catálogo/comparación de precios que alimenta el scraper; no escribe en la BBDD que consulta salvo en los endpoints de escritura explícitos (matching, suscripciones, administración).

Estado actual: toda la superficie de `docs/api-endpoints-v1.md` + `docs/api-endpoints-gestor.md` implementada, salvo `POST /stores/{id}/scrape` (aplazado explícitamente -- ver "Endpoints pendientes" más abajo).

## Instalación

```bash
pip install -r requirements.txt
```

`requirements.txt` incluye `-e ../shared` (dominio + clasificación compartidos con `store_monitor/`, ver sección "Arquitectura" más abajo) -- necesita que `shared/` exista como carpeta hermana de `api/`, igual que ya asumía el resto del repo.

Requiere el mismo Postgres que `store_monitor/` (ver `docker_composes/docker-compose.yml` en la raíz del repo) -- comparten esquema y datos, no hay una BBDD propia de la API.

## Configuración

| Variable | Default | Qué es |
|---|---|---|
| `DATABASE_URL` | `postgresql://cartitas:cartitas@localhost:5433/cartitas` | Conexión a Postgres |
| `API_KEYS_JSON` | `{}` (ninguna key válida) | `{"clave": ["read", "write:subscriptions"], ...}` -- API keys estáticas por cliente y sus scopes (ver `docs/api-endpoints-v1.md` sección 0). Los scopes usados hoy: `read`, `write:subscriptions`, `admin:*` (panel de revisión/gestor, un único scope amplio -- ver `docs/api-endpoints-gestor.md` sección 0) |

## Uso

```bash
uvicorn main:app --reload
```

Documentación interactiva en `/docs` (Swagger UI) y `/redoc`, generada automáticamente del código -- no hay `openapi.yaml` escrito a mano.

## Arquitectura

Capas de dependencia unidireccional, misma disciplina que `store_monitor/` (ver `docs/estandares_organizacion_codigo.md`), adaptada al patrón de FastAPI (ver `docs/estandares-implementacion-api.md`, sección 2):

```
models/ (SQLModel, reflejo del esquema SQL, sin lógica)
      ↑
schemas/ (Pydantic request/response, camelCase vía CamelModel)
      ↑
services/ (lógica de negocio + queries -- funciones de Python normales, sin saber que existe HTTP)
      ↑
routers/ (solo parsean la petición, llaman al service, devuelven la respuesta)
      ↑
main.py (junta routers, registra el exception handler, CORS)
```

- `config.py` / `db.py` -- conexión a Postgres y API keys, sin lógica de negocio.
- `auth.py` -- `require_scope(scope)`, enganchado por `Depends()` en cada router.
- `errors.py` -- excepciones de dominio (`NotFoundError`, `ConflictError`...) + su mapeo único a `application/problem+json` (RFC 7807).
- `pagination.py` -- envelope `{data, meta}` + `Link` header (RFC 8288), compartido por todo listado.
- `services/matches.py` depende de `shared` (`from shared.classify import ...`) -- paquete instalable hermano de `api/` y `store_monitor/` (`../shared`, ver su `pyproject.toml`) que contiene solo dominio + reglas de clasificación puras, sin dependencias de terceros. Es una dependencia real declarada en `requirements.txt` (Shared Kernel de DDD), no un `sys.path` hack: así `GET /matches` deriva la categoría de un `raw_name` con la MISMA lógica que `matcher.run_matching()` sin arrastrar `cloudscraper`/`pybreaker`/el resto de dependencias del scraper.

**Regla que se mantiene al añadir el siguiente endpoint:** un router nunca contiene un `select`/regla de negocio inline -- eso vive en `services/`, testeable sin FastAPI ni `TestClient`.

## Endpoints implementados

| Endpoint | Auth | Descripción |
|---|---|---|
| `GET /products` | `read` | Buscador de productos canónicos, con filtros (`q`, `game`, `category`, `setCode`, `language`, `minPrice`, `maxPrice`, `isHot`) y paginación. Solo cuentan `storeProduct` confirmados para `minPrice`/`storeCount`/`anyInStock`. |
| `GET /products/{id}` | `read` | Ficha con `listings` ordenados de más barato a más caro. `404` si no hay ningún listado confirmado. |
| `GET /products/{id}/price-history` | `read` | Curva agregada (mínimo entre tiendas) o de una tienda concreta (`storeId`). |
| `POST /products` | `admin:*` | Alta de producto canónico a mano. `409` si colisiona `gameId`+`nameCanonical`. |
| `PATCH /products/{id}` | `admin:*` | Edición parcial (`nameCanonical`, `imageUrl`, `isHot`, `hotUntil`) -- `categoryId`/`gameId`/`setCode` no editables en v1. |
| `GET /deals` | `read` | Ranking de bajadas de precio vs. hace 7 días exactos, solo productos con stock ahora mismo. |
| `GET /restock-events` | `read` | Feed público de altas de stock recientes (ventana en horas). |
| `GET /stores` / `GET /stores/{id}` | `read` | Listado y detalle (con campos dinámicos de scraping respetuoso: `crawlDelaySeconds`, `disallowed`, etc.). |
| `PATCH /stores/{id}` | `admin:*` | Edita `sitemapUrl`/`active`. `active=false` SÍ excluye la tienda del próximo barrido (cableado en `store_monitor/dispatcher.py`). |
| `GET /games`, `GET /categories` | `read` | Catálogo de filtros; `categories` como árbol de dos niveles. |
| `POST /subscriptions` | `write:subscriptions` | Sin `Idempotency-Key` real -- se apoya en `UNIQUE NULLS NOT DISTINCT(productId, storeId, pushEndpoint)`, `409` en reintento (ver `docs/estandares-implementacion-api.md` sección 7). |
| `DELETE /subscriptions/{id}` | `write:subscriptions` | Requiere `pushEndpoint` como prueba de propiedad (`403` si no coincide). |
| `GET /subscriptions?pushEndpoint=` | `read` | "Productos que sigues" de un dispositivo, sin cuenta de usuario. |
| `GET /matches` | `admin:*` | Cola de matching -- `status` en `needsReview`/`unmatched`/`confirmed`/`all`, `minSimilarity`/`maxSimilarity` sobre el top-1 candidato calculado en caliente (nunca sobre `matchConfidence`), oculta lo revisado hace menos de 14 días salvo `includeReviewed=true`. |
| `POST /matches/{id}/confirm` \| `/reject` \| `/reopen` | `admin:*` | Ciclo completo de revisión manual -- `reopen` exige que estuviera `confirmed` (`409` si no). |
| `GET /matches/missing-candidates` | `admin:*` | Puerto de `matcher.find_missing_canonical_candidates()` (C.1) sobre SQLModel. |

## Endpoints pendientes

- `POST /stores/{id}/scrape` (`docs/api-endpoints-gestor.md` sección 3) -- expondría `dispatcher.query_store()`, pero `api/` no tiene (a propósito) las dependencias de scraping de `store_monitor/`. Aplazado como su propia tarea de diseño hasta decidir el puente entre los dos servicios (subproceso, cola de trabajos...).

## Tests

```bash
docker exec cartitas-postgres psql -U cartitas -d cartitas -c "CREATE DATABASE cartitas_test;"
docker exec -i cartitas-postgres psql -U cartitas -d cartitas_test < ../schema-postgresql-app-tcg.sql
pip install -r requirements-dev.txt
pytest
```

Misma `cartitas_test` que usa `store_monitor/tests/` (mismo contenedor, mismo esquema) -- no hace falta una base de datos de test separada por servicio. Sin Postgres accesible, los tests que la necesitan se saltan con `pytest.skip`, igual que en `store_monitor/`.

```bash
pytest --cov=. --cov-report=term-missing --cov-config=<(printf '[run]\nomit = tests/*,conftest.py')
```

140 tests, 99% de cobertura. Un fichero de test por área funcional, cada uno con una clase de tests de `services/` (integración contra Postgres real, sin mocks de la sesión) y otra de `routers/` (`TestClient` contra la misma BBDD -- auth, `camelCase` de verdad en el JSON, códigos de estado, `problem+json`):

- `test_products_service.py` / `test_products_router.py` -- búsqueda, ficha, histórico, alta/edición de administración.
- `test_deals.py`, `test_restock_events.py`, `test_catalog.py`, `test_stores.py`, `test_subscriptions.py`, `test_matches.py`.
- `test_auth.py` -- unitario puro, sin BBDD.
- `test_errors.py` -- un test por tipo de excepción -> código HTTP + forma del `problem+json`, contra una app FastAPI mínima propia (no la real).

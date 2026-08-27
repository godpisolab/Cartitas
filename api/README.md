# Cartitas API

Servicio FastAPI + SQLModel, hermano de `store_monitor/` (no un módulo dentro de él -- ver `docs/estandares-implementacion-api.md`, sección 1). Sirve el catálogo/comparación de precios que alimenta el scraper; no escribe en la BBDD que consulta salvo en los endpoints de escritura explícitos (matching, suscripciones, administración).

Estado actual: toda la superficie de `docs/api-endpoints-v1.md` + `docs/api-endpoints-gestor.md` implementada, salvo `POST /stores/{id}/scrape` (aplazado explícitamente -- ver "Endpoints pendientes" más abajo). Panel de gestor completo (matching, productos, tiendas) también implementado -- ver "Panel de gestor" más abajo.

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
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `""` (ninguna credencial válida) | Credencial de PERSONA para `/admin/*` (HTTP Basic) -- deliberadamente separada de `API_KEYS_JSON`, ver "Panel de gestor" más abajo |

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
| `GET /stores` / `GET /stores/{id}` | `read` | Listado y detalle (con campos dinámicos de scraping respetuoso: `sitemapUrl`, `crawlDelaySeconds`, `disallowed`, etc. -- `sitemapUrl` añadido a `StoreDetail` el 2026-08-27, faltaba pese a que `PATCH` ya lo aceptaba). |
| `PATCH /stores/{id}` | `admin:*` | Edita `sitemapUrl`/`active`. `active=false` SÍ excluye la tienda del próximo barrido (cableado en `store_monitor/dispatcher.py`). |
| `GET /games`, `GET /categories` | `read` | Catálogo de filtros; `categories` como árbol de dos niveles. |
| `POST /subscriptions` | `write:subscriptions` | Sin `Idempotency-Key` real -- se apoya en `UNIQUE NULLS NOT DISTINCT(productId, storeId, pushEndpoint)`, `409` en reintento (ver `docs/estandares-implementacion-api.md` sección 7). |
| `DELETE /subscriptions/{id}` | `write:subscriptions` | Requiere `pushEndpoint` como prueba de propiedad (`403` si no coincide). |
| `GET /subscriptions?pushEndpoint=` | `read` | "Productos que sigues" de un dispositivo, sin cuenta de usuario. |
| `GET /matches` | `admin:*` | Cola de matching -- `status` en `needsReview`/`unmatched`/`confirmed`/`all`, `minSimilarity`/`maxSimilarity` sobre el top-1 candidato calculado en caliente (nunca sobre `matchConfidence`), oculta lo revisado hace menos de 14 días salvo `includeReviewed=true`. |
| `POST /matches/{id}/confirm` \| `/reject` \| `/reopen` | `admin:*` | Ciclo completo de revisión manual -- `reopen` exige que estuviera `confirmed` (`409` si no). `reject` persiste `reason` en `store_product.reviewed_reason` desde el 2026-08-27 (antes se aceptaba en el body y se descartaba en silencio). |
| `GET /matches/missing-candidates` | `admin:*` | Puerto de `matcher.find_missing_canonical_candidates()` (C.1) sobre SQLModel. |

## Panel de gestor

HTML server-rendered (Jinja2 + htmx en matching, formularios de página completa en productos/tiendas) dentro del propio proceso de `api/`, no una SPA aparte -- decisión y por qué en `docs/frontend-arquitectura-decidida.md` sección 3, cómo se organiza en `docs/estandares-implementacion-frontend.md` parte 2, cierre de fase completo en `docs/plan-cierre-panel-gestor.md`.

- `admin/auth.py` -- HTTP Basic (`ADMIN_USERNAME`/`ADMIN_PASSWORD`), mecanismo distinto del Bearer+scope de `auth.py`. Aplicado una única vez en `main.py` (`dependencies=[Depends(verify_admin)]` por router de `/admin`), nunca a mano dentro de una ruta. **Falla cerrado si no se configuran las variables** -- sin esto, `ADMIN_USERNAME`/`ADMIN_PASSWORD` a `""` (default sin configurar) haría que `compare_digest("", "")` fuera `True` y unas credenciales vacías (`curl -u ":"`) entraran como admin; `verify_admin()` rechaza explícitamente el caso "ninguna de las dos está configurada" antes de comparar.
- Todas las rutas de `admin/routes/*.py` llaman a `services/*.py` directamente, sin pasar por HTTP ni por ninguna API key.
- **Matching** (`admin/routes/matches.py`) -- `GET /admin/matches` (listado con filtro `status` visible), `POST /admin/matches/{id}/confirm|reject|reopen` (htmx, cada uno devuelve el fragmento `_row.html` que se intercambia en la fila; `reject` acepta `mark_as` + `reason` opcional, persistido en `reviewed_reason`), `GET /admin/missing-candidates` (con enlace "Crear canónico" que preellena el alta de producto vía query params).
- **Productos** (`admin/routes/products.py`) -- `GET /admin/products` (buscador simple), `GET /admin/products/new` + `POST /admin/products` (alta), `GET /admin/products/{id}/edit` + `POST /admin/products/{id}` (edición). Formulario de página completa compartido entre alta/edición (`products/form.html`); un `409` por nombre duplicado vuelve a mostrar el formulario con lo ya escrito, nunca la página de error genérica de FastAPI.
- **Tiendas** (`admin/routes/stores.py`) -- `GET /admin/stores` (listado con columna de salud: `lastScrapedAt`, `consecutiveFailures`), `GET /admin/stores/{id}` + `POST /admin/stores/{id}` (detalle + edición de `sitemapUrl`/`active`).
- CSS mínimo sin build step en `admin/static/`; IP allowlist deliberadamente fuera de `api/` (vive en el reverse proxy de despliegue, aún sin decidir).
- **`POST /stores/{id}/scrape` no forma parte del panel** -- mismo motivo que en "Endpoints pendientes" más abajo.

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

197 tests, 99% de cobertura. Un fichero de test por área funcional, cada uno con una clase de tests de `services/` (integración contra Postgres real, sin mocks de la sesión) y otra de `routers/` (`TestClient` contra la misma BBDD -- auth, `camelCase` de verdad en el JSON, códigos de estado, `problem+json`):

- `test_products_service.py` / `test_products_router.py` -- búsqueda, ficha, histórico, alta/edición de administración.
- `test_deals.py`, `test_restock_events.py`, `test_catalog.py`, `test_stores.py`, `test_subscriptions.py`, `test_matches.py`.
- `test_admin_matches.py` -- panel de matching: HTTP Basic (autenticado/no autenticado/sin configurar), filtro de `status`, listado HTML, ciclo confirmar/rechazar/reabrir devolviendo el fragmento `_row.html`, formulario de rechazo con `reason`, `missing-candidates`.
- `test_admin_products.py` -- panel de productos: listado/búsqueda, alta con prellenado desde query params, `409` por nombre duplicado sin perder lo escrito, edición (incluido `isHot`/`hotUntil`), 404 sobre producto inexistente.
- `test_admin_stores.py` -- panel de tiendas: listado con columna de salud, edición de `sitemapUrl`/`active`.
- `test_auth.py` -- unitario puro, sin BBDD.
- `test_errors.py` -- un test por tipo de excepción -> código HTTP + forma del `problem+json`, contra una app FastAPI mínima propia (no la real).

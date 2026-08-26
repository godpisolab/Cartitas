# Cartitas API

Servicio FastAPI + SQLModel, hermano de `store_monitor/` (no un módulo dentro de él -- ver `docs/estandares-implementacion-api.md`, sección 1). Sirve el catálogo/comparación de precios que alimenta el scraper; no escribe en la BBDD que consulta salvo en los endpoints de escritura explícitos (matching, suscripciones).

Estado actual: **esqueleto + `GET /products`**, el primer endpoint end-to-end (auth, paginación, errores RFC 7807 y `camelCase` ya validados en un caso real) antes de replicar el patrón al resto de `docs/api-endpoints-v1.md`.

## Instalación

```bash
pip install -r requirements.txt
```

Requiere el mismo Postgres que `store_monitor/` (ver `docker_composes/docker-compose.yml` en la raíz del repo) -- comparten esquema y datos, no hay una BBDD propia de la API.

## Configuración

| Variable | Default | Qué es |
|---|---|---|
| `DATABASE_URL` | `postgresql://cartitas:cartitas@localhost:5433/cartitas` | Conexión a Postgres |
| `API_KEYS_JSON` | `{}` (ninguna key válida) | `{"clave": ["read", "write:subscriptions"], ...}` -- API keys estáticas por cliente y sus scopes (ver `docs/api-endpoints-v1.md` sección 0) |

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

**Regla que se mantiene al añadir el siguiente endpoint:** un router nunca contiene un `select`/regla de negocio inline -- eso vive en `services/`, testeable sin FastAPI ni `TestClient`.

## Endpoints implementados

| Endpoint | Auth | Descripción |
|---|---|---|
| `GET /products` | `read` | Buscador de productos canónicos, con filtros (`q`, `game`, `category`, `setCode`, `language`, `minPrice`, `maxPrice`, `isHot`) y paginación. Solo cuentan `storeProduct` con `matchStatus = confirmed` para `minPrice`/`storeCount`/`anyInStock` -- un producto sin ningún listado confirmado no aparece en absoluto. |

El resto de `docs/api-endpoints-v1.md` (ficha de producto, histórico, ofertas, tiendas, catálogo de filtros, suscripciones, panel de revisión) está especificado pero pendiente de implementar.

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

- `test_products_service.py` -- integración contra Postgres real: agregados (`minPrice`/`storeCount`/`anyInStock` solo sobre confirmados), todos los filtros, paginación.
- `test_products_router.py` -- `TestClient` contra la misma BBDD: auth (401/403/200), `camelCase` de verdad en el JSON, `Link` header, validación de query params (422 `problem+json`).
- `test_auth.py` -- unitario puro, sin BBDD.
- `test_errors.py` -- un test por tipo de excepción -> código HTTP + forma del `problem+json`, contra una app FastAPI mínima propia (no la real).

# Guías de implementación — API (FastAPI + SQLModel)

Aplica los mismos principios de `docs/estandares_organizacion_codigo.md` (dirección de dependencias en una sola vía, módulos de una responsabilidad, tests en el mismo commit que el código) al servicio nuevo, adaptados a lo que FastAPI/SQLModel ya deciden por ti. No repite las convenciones de protocolo (`camelCase`, RFC 7807, paginación, auth) — esas ya están en `estandares-api-app-tcg.md` y `api-endpoints-v1.md`; esto es solo el **cómo se organiza el código que las implementa**.

---

## 1. Es un servicio separado del scraper, no un módulo dentro de `store_monitor/`

El scraper y la API tienen ciclos de vida, dependencias y formas de fallar completamente distintos: uno es un proceso batch/programado (`scheduler.py`), el otro un servidor web de request/response. Meter la API dentro de `store_monitor/` repetiría exactamente el error que ya corregisteis con `base_script.py` — mezclar dos responsabilidades que no tienen por qué compartir ni un solo fichero.

```
Cartitas/
├── store_monitor/          -- ya existe, sin tocar
├── api/                    -- nuevo, paquete hermano, no hijo
│   ├── main.py
│   ├── config.py
│   ├── db.py
│   ├── auth.py
│   ├── errors.py
│   ├── pagination.py
│   ├── models/
│   ├── schemas/
│   ├── routers/
│   ├── services/
│   ├── requirements.txt
│   └── tests/
├── schema-postgresql-app-tcg.sql
└── seed-catalog-app-tcg.sql
```

`api/requirements.txt` es su propio fichero, no una extensión de `store_monitor/requirements.txt` — la API no necesita `cloudscraper`/`pybreaker`/`apscheduler`, y el scraper no necesita `fastapi`/`sqlmodel`/`uvicorn`. Compartir un único `requirements.txt` para ambos serviría exactamente al mismo problema que ya resolvisteis separando `base_script.py`: instalar en producción dependencias que ese proceso concreto no usa.

---

## 2. Capas dentro de `api/` — la misma disciplina de dirección única, aplicada al patrón MVC-ish de FastAPI

```
domain (SQLModel table models)
      ↑
schemas (Pydantic request/response, camelCase)
      ↑
services (lógica de negocio + queries -- SIN saber que existe HTTP)
      ↑
routers (SOLO parsean la petición, llaman al service, devuelven la respuesta)
      ↑
main.py (junta routers, registra el exception handler, CORS, arranque)
```

**Regla concreta: un router nunca contiene una consulta SQL/ORM ni una regla de negocio.** Si al escribir un endpoint te encuentras escribiendo un `session.exec(select(...))` dentro de la función del router, esa línea pertenece a `services/`, no a `routers/`. La prueba de que esta separación es real (no solo estética): un test de `services/products.py` no debería necesitar importar FastAPI ni levantar un `TestClient` — es una función de Python normal que recibe una sesión de BBDD y devuelve datos.

```python
# routers/products.py -- fino, solo HTTP
@router.get("/products", response_model=Page[ProductSummary])
def list_products(
    filters: ProductFilters = Depends(),
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read:products")),
):
    return products_service.search(session, filters)

# services/products.py -- la lógica de verdad, testeable sin HTTP
def search(session: Session, filters: ProductFilters) -> Page[ProductSummary]:
    query = select(Product).join(StoreProduct, ...).where(StoreProduct.match_status == "confirmed")
    ...
    return Page(data=[...], meta=PageMeta(page=filters.page, limit=filters.limit, total=total))
```

---

## 3. `models/` vs `schemas/` — no son lo mismo, aunque SQLModel tiente a fusionarlos

SQLModel permite que una misma clase sea a la vez tabla de BBDD y modelo de Pydantic — cómodo, pero mezcla otra vez dos responsabilidades: "cómo se guarda" y "qué ve el cliente por API" no son siempre el mismo shape (`GET /products` no expone `id_product` interno de cada tienda, `POST /matches/{id}/confirm` no espera ni de lejos los mismos campos que tiene la fila completa de `store_product`).

**Regla: `models/` (SQLModel `table=True`) son el reflejo del esquema de Postgres, un fichero por tabla, sin lógica. `schemas/` son clases Pydantic normales (no tablas) para lo que entra y sale de cada endpoint, con `alias_generator=to_camel` centralizado una vez, no repetido endpoint por endpoint.**

```python
# schemas/common.py -- una sola vez para todo el proyecto
class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

class PageMeta(CamelModel):
    page: int
    limit: int
    total: int

class Page(CamelModel, Generic[T]):
    data: list[T]
    meta: PageMeta
```

Todo schema de respuesta hereda de `CamelModel` — así el `camelCase` decidido en `estandares-api-app-tcg.md` se aplica en un único sitio, nunca a mano renombrando campos dentro de un router.

**Decidido (2026-08-27):** `schema-postgresql-app-tcg.sql` sigue siendo la única fuente de verdad del esquema. `models/` se escribe a mano para **reflejar** lo que ya existe en la BBDD, nunca al revés — ningún `SQLModel` de `models/` genera ni propone cambios de esquema por su cuenta. Consecuencia práctica: cada vez que cambie el `.sql`, el reflejo en `models/` se actualiza a mano como parte del mismo cambio (mismo commit), igual que ya se hace hoy con cualquier consumidor del esquema. Esto mantiene la decisión de Alembic completamente independiente de la de la API — cuando llegue el momento de meter Alembic, seguirá siendo el `.sql`/las migraciones quien manda, y `models/` seguirá siendo un reflejo, no la fuente.

---

## 4. Auth como dependencia reutilizable, nunca comprobada a mano en cada router

```python
# auth.py
def require_scope(scope: str):
    def _check(authorization: str = Header(...)) -> None:
        key = validate_api_key(authorization)  # 401 si no existe/mal formada
        if scope not in key.scopes:
            raise ForbiddenError(scope)         # 403 -- ver errors.py
    return _check
```

Cada router declara qué scope necesita en su propia firma (`Depends(require_scope("write:matches"))`) — el scope requerido queda documentado en el propio endpoint, no en una tabla aparte que hay que mantener sincronizada a mano.

---

## 5. Errores: excepciones de dominio + un único exception handler, nunca `HTTPException` disperso por los services

**Regla: `services/` levanta excepciones con nombre de dominio (`ProductNotFoundError`, `DuplicateSubscriptionError`), nunca `HTTPException` directamente.** El mapeo a código HTTP + formato RFC 7807 vive en un único sitio (`errors.py`, registrado una vez en `main.py`), para que la tabla de "qué código HTTP corresponde a qué situación" no se disperse por todos los `routers/`.

```python
# errors.py
class NotFoundError(Exception): ...
class ConflictError(Exception): ...
class UnprocessableEntityError(Exception): ...

@app.exception_handler(NotFoundError)
def handle_not_found(request, exc):
    return problem_json_response(status=404, title=str(exc))
```

Esto también hace trivial testear un `service` en aislamiento: el test comprueba que se lanza `ProductNotFoundError`, no que la respuesta HTTP tiene tal código — el mapeo a HTTP se testea una sola vez, en `test_errors.py`, no repetido en cada test de cada endpoint.

---

## 6. Paginación: un `Depends()` compartido, no reimplementado por endpoint

```python
# pagination.py
class PageParams(BaseModel):
    page: int = Query(1, ge=1)
    limit: int = Query(20, ge=1, le=100)

def build_link_header(request: Request, page: int, limit: int, total: int) -> str: ...
```

Todo router que pagine (`/products`, `/matches/pending`, `/subscriptions`...) usa el mismo `PageParams` y la misma función de `Link` header — si mañana cambia el límite máximo o el formato del `Link`, se cambia en un sitio.

---

## 7. Idempotencia — recomendación de simplificar para v1, no construir infraestructura genérica todavía

`api-endpoints-v1.md` pide `Idempotency-Key` en `POST /subscriptions`. Implementarlo de verdad (tabla de claves ya vistas + respuesta cacheada + expiración) es infraestructura genérica no trivial para un único endpoint que ya tiene una `UNIQUE(productId, storeId, pushEndpoint)` en el esquema.

**Propuesta para v1:** apoyarse en esa constraint como mecanismo de idempotencia de facto — si el cliente reintenta la misma suscripción, recibe `409`, y el propio frontend interpreta un `409` en este endpoint como "ya estás suscrito" (puede hacer un `GET /subscriptions?pushEndpoint=` para recuperar el `id` si lo necesita) en vez de tratarlo como error real. Esto evita construir una tabla de `idempotency_record` genérica antes de tener un segundo caso de uso real que la justifique. Si más adelante aparece un endpoint donde el reintento SÍ pueda crear un efecto secundario distinto cada vez (no es el caso aquí), ese es el momento de construir la infraestructura genérica.

**Diferido a propósito (2026-08-27):** no se decidió en abstracto -- se revisó contra el commit real que implementó `POST /subscriptions`. Conclusión: la simplificación de apoyarse en el `409` basta en la práctica (no hay todavía un segundo endpoint de escritura donde un reintento pudiera crear un efecto secundario distinto cada vez), así que **no** se construyó la tabla `idempotency_record` -- `POST /subscriptions` tampoco exige la cabecera `Idempotency-Key` en la implementación final, para no pedir un dato que no se usa para nada real (ver `api/schemas/subscriptions.py`). De paso se encontró y corrigió el mismo bug de `NULLS NOT DISTINCT` que ya se había arreglado una vez en `store_product`: el `UNIQUE(productId, storeId, pushEndpoint)` de `restock_subscription` nunca detectaba conflicto cuando `storeId` era `NULL` (el único valor que existe en v1), así que la constraint en sí no habría bastado sin ese arreglo. Si en el futuro aparece un caso real que sí lo justifique, esa es la señal para construir la infraestructura genérica -- no antes.

---

## 8. Testing — mismo rigor que el scraper, no un nivel más bajo porque "es solo la API"

Ya tenéis el patrón correcto documentado y probado en `store_monitor/tests/` — se traslada tal cual:

- **`services/`**: tests de integración contra Postgres real (mismo `cartitas_test` + fixtures de `tests/conftest.py` del scraper, reutilizables) — nunca mockear la sesión de SQLModel. El riesgo real está en si la query hace lo que crees, igual que ya se decidió para `persistence.py`.
- **`routers/`**: tests con `TestClient` de FastAPI contra la misma BBDD real, verificando serialización (`camelCase` de verdad en el JSON de respuesta, no solo en el modelo Python), códigos de estado, y que el `Link` header sale bien formado.
- **`auth.py`**: unitario puro — validar/rechazar keys y scopes sin tocar BBDD.
- **`errors.py`**: un test por tipo de excepción → código HTTP + forma del `problem+json`, una vez, no repetido por endpoint.

Ningún router/service nuevo se mergea sin sus tests en el mismo commit — mismo estándar que ya aplicasteis al scraper.

---

## 9. Checklist antes de añadir un endpoint nuevo

- [ ] ¿El router es fino (parsea + llama a un `service` + devuelve), sin ningún `select`/regla de negocio inline?
- [ ] ¿El schema de respuesta hereda de `CamelModel` (o equivalente), en vez de renombrar campos a mano?
- [ ] ¿El scope de auth necesario está declarado en la firma del endpoint vía `Depends(require_scope(...))`?
- [ ] ¿Los errores esperables (`404`, `409`, `422`...) se expresan como excepciones de dominio, no como `HTTPException` construida a mano dentro del router?
- [ ] ¿Tiene tests de `service` (integración contra Postgres real) y, si expone algo nuevo de serialización/paginación, también de `router` (`TestClient`)?

---

## Siguiente paso natural

Con esto, el primer PR razonable no es "toda la API de golpe" — es el esqueleto (`main.py`, `db.py`, `auth.py`, `errors.py`, `pagination.py`, `schemas/common.py`) más UN solo endpoint end-to-end (`GET /products` es buen candidato: toca lectura, paginación, y el filtro por `matchStatus = confirmed`, sin tocar aún ni auth de escritura ni idempotencia) para validar que las cinco piezas encajan antes de replicar el patrón al resto.

**Hecho (2026-08-27, primera pasada):** esqueleto completo + `GET /products` end-to-end, siguiendo exactamente esta guía. 40 tests, 99% de cobertura. CI extendido con un job `test-api` paralelo al del scraper.

**Hecho (2026-08-27, resto de la superficie):** replicado el mismo patrón a todo `api-endpoints-v1.md` + `api-endpoints-gestor.md` salvo `POST /stores/{id}/scrape` (aplazado, ver esa sección más abajo). Incluye la migración de `store_product.reviewed_at` (`ALTER TABLE` directo, sin Alembic) y el cableado real de `store.active` en `dispatcher.run_all_stores()` (`store_monitor/`, no `api/` -- pero motivado por `PATCH /stores/{id}`). Para reutilizar la ÚNICA fuente de verdad de `classify_product()`/`matcher.py` en `GET /matches` (top-3 de candidatos, `missing-candidates`) sin arrastrar las dependencias pesadas del scraper, se añadió `api/_store_monitor_bridge.py`: un import puntual y documentado de solo los módulos puros de `store_monitor/` (`classify.py`, las constantes de `matcher.py`), con `sys.path.append()` (nunca `insert(0, ...)`) para que el `config.py` propio de `api/` siga ganando cualquier resolución de import posterior pese a que ambos paquetes comparten ese nombre de módulo. 140 tests, 99% de cobertura.

**Reemplazado (2026-08-26):** ese bridge por `sys.path` acoplaba `api/` a `store_monitor/` como dependencia de sistema de ficheros no declarada, sin ninguna garantía estructural de que se mantuviera libre de dependencias pesadas. `api/_store_monitor_bridge.py` ya no existe -- `domain.py`/`classify.py` se movieron a un paquete `shared/` nuevo (dependencia editable real, declarada en el `requirements.txt` de ambos servicios), ver `docs/estandares_organizacion_codigo.md` y el propio `api/README.md` sección "Arquitectura". Mismo principio, mecanismo distinto y más explícito.

**Actualización (2026-08-26):** `api/_store_monitor_bridge.py` se eliminó -- el `sys.path.append()` era un acoplamiento de filesystem no declarado (ver decisión de arquitectura sobre el acoplamiento `api/`/`store_monitor/`). `domain.py`/`classify.py` se mudaron a un paquete nuevo `shared/`, hermano de `api/` y `store_monitor/` (patrón Shared Kernel de DDD), instalado como dependencia editable real (`-e ../shared`) en el `requirements.txt` de ambos servicios. `api/services/matches.py` ahora hace `from shared.classify import classify_with_category, ...` directamente, sin puente. De paso se centralizó `classify_product() + PRODUCT_TYPE_TO_CATEGORY_SLUG.get(...)` (que estaba duplicado, idéntico, en `matcher._evaluate()` y en `_candidates_for()`) en `shared.classify.classify_with_category()`.

# Estándares y protocolos para la API

## 1. Estilo de API: REST + OpenAPI

**Recomendación: REST convencional, documentado con OpenAPI (Swagger) desde el diseño, no después de construirla.**

- **REST** encaja de forma natural con el listado de endpoints que ya hicimos (orientado a recursos: `/products`, `/stores`, `/subscriptions`...). Alternativas consideradas:
  - **GraphQL** — más flexible (el cliente pide exactamente los campos que necesita en una sola petición, útil cuando hay anidamiento como producto→listados de tienda→histórico), pero añade complejidad de schema/resolvers que no está justificada a esta escala.
  - **JSON:API** (estándar formal sobre REST, `jsonapi.org`) — define convenciones para relaciones, paginación e "includes" (traer recursos relacionados en la misma respuesta). Interesante porque ya tenemos relaciones reales (producto ↔ store_product), pero es más rígido de lo que hace falta para empezar.
  - Ninguna de las dos aporta lo suficiente ahora mismo frente a la simplicidad de REST plano.
- **OpenAPI** (antes Swagger) es el estándar de facto para especificar una API REST de forma que sea legible por máquina: define cada endpoint, sus parámetros, tipos de respuesta y códigos de error en un único documento (`openapi.yaml`). Ventaja directa para ti: de ese documento se puede generar automáticamente la documentación interactiva, y también clientes tipados para el frontend, sin escribirlos a mano. Es el paso natural siguiente a la lista de endpoints que ya redactamos — se puede traducir directamente a un fichero OpenAPI.

---

## 2. Convenciones HTTP a nivel de protocolo

| Estándar | Qué resuelve | Aplicación aquí |
|---|---|---|
| **Códigos de estado HTTP correctos** | Comunicar el resultado sin depender del cuerpo de la respuesta | `200` lectura OK, `201` creación (ej. `POST /subscriptions`), `204` borrado sin contenido (`DELETE /subscriptions/{id}`), `400` petición mal formada, `401` sin API key o key inválida, `403` key válida pero sin permiso para esa acción, `404` recurso no existe, `409` conflicto (ej. suscripción duplicada), `422` datos válidos en formato pero semánticamente incorrectos, `429` rate limit excedido |
| **RFC 7807 — Problem Details for HTTP APIs** | Formato estándar para errores, en vez de inventar uno propio | Toda respuesta de error usa `Content-Type: application/problem+json` con `{type, title, status, detail, instance}` — cualquier cliente (incluida una app futura) sabe interpretar el error sin documentación adicional |
| **Paginación con envelope + `Link` header (RFC 8288)** | Estándar para listados largos (`/products`, `/matches/pending`) | Respuesta con `{data: [...], meta: {page, limit, total}}` y cabecera `Link: <...?page=2>; rel="next"` |
| **Cabeceras de rate limit** (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `Retry-After`) | Que un cliente sepa cuánto margen tiene antes de que le bloquees, sin tener que adivinarlo | Mismo patrón que ya aplicamos al scraper (`429` + `Retry-After`) — aquí es la API la que se protege de un cliente demasiado agresivo, sea el frontend con un bug o una app futura |
| **Idempotencia con `Idempotency-Key`** | Evitar duplicados si el cliente reintenta una petición que sí llegó a procesarse (ej. fallo de red justo después de crear la suscripción) | Aplicable sobre todo a `POST /subscriptions` — el navegador puede reintentar el registro de push sin miedo a crear dos suscripciones iguales. **Diferido (2026-08-27, `estandares-implementacion-api.md` sección 7):** en la implementación final no se pide esta cabecera — el `UNIQUE(productId, storeId, pushEndpoint)` del esquema ya basta como idempotencia de facto (`409` en reintento) |
| **CORS configurado explícitamente** | La API la van a llamar el Frontend público y el Panel de revisión, probablemente en dominios/subdominios distintos | Lista blanca de orígenes permitidos por cliente, no `Access-Control-Allow-Origin: *` |

---

## 3. Autenticación: convención Bearer Token (sin implementar OAuth2 completo)

Reutilizamos la convención de transporte de **OAuth 2.0 Bearer Token (RFC 6750)** — cabecera `Authorization: Bearer <api_key>` — sin implementar el flujo completo de OAuth2 (no hace falta *authorization server*, refresh tokens, ni consentimiento de usuario para esto). Es el estándar que cualquier herramienta HTTP y cualquier desarrollador reconoce de inmediato, aunque la "clave" en sí sea simplemente una API key estática por cliente como ya decidimos.

---

## 4. Notificaciones push: Web Push Protocol + VAPID

Esto es un estándar que **ya estás usando de facto** desde que decidimos "push sin login", pero conviene nombrarlo explícitamente porque tiene una pieza más:

- **Web Push Protocol (RFC 8030)** — define cómo un servidor entrega un mensaje a través del servicio push del navegador (`push_endpoint` + `push_keys` que ya tenemos en el esquema son parte de este estándar).
- **VAPID (RFC 8292)** — el mecanismo con el que tu backend se identifica ante el servicio push del navegador (Google/Mozilla) sin depender de Firebase Cloud Messaging ni de ningún intermediario propietario. Necesitas generar un par de claves VAPID (pública/privada) una vez, la pública se la das al frontend al suscribirse, la privada la usa tu backend para firmar cada envío.

No hay alternativa real aquí — es el único estándar abierto para push web sin atarte a Firebase.

---

## 5. Convención de nombres en JSON: `camelCase` (decidido)

La base de datos usa `snake_case` (convención de Postgres). Para el JSON de la API se decidió **exponer `camelCase`** — más idiomático para los clientes JavaScript/TypeScript que serán el frontend y el panel de revisión.

Esto exige una capa de mapeo en la API (BBDD en `snake_case` → JSON en `camelCase`). Con SQLModel/Pydantic se resuelve con un alias generator (`alias_generator=to_camel` + `populate_by_name=True` en la config del modelo, o `response_model_by_alias=True` en FastAPI) en vez de renombrar campos a mano en cada endpoint — la capa de traducción vive en la definición del modelo, no dispersa por el código.

---

## 6. Framework elegido: FastAPI

Decisión tomada: la API se construye en **FastAPI**. Encaja directamente con los estándares ya fijados en este documento — resumen de cómo se cubre cada uno:

| Estándar / necesidad | Cómo lo cubre FastAPI |
|---|---|
| OpenAPI | Generado automáticamente a partir del código (type hints + Pydantic) — no se escribe `openapi.yaml` a mano, sale del propio endpoint tipado. Documentación interactiva gratis en `/docs` (Swagger UI) y `/redoc` |
| RFC 7807 (errores) | No viene de fábrica — se añade con un *exception handler* personalizado que devuelve `application/problem+json` |
| Auth por API key + scope | Sistema de `Depends()` — una dependencia valida la key y su scope, se engancha declarativamente por endpoint (ej. `Depends(require_scope("write:subscriptions"))`) |
| CORS | Incluido de serie (`CORSMiddleware`, heredado de Starlette) |
| Rate limiting | No incluido — se añade con `slowapi` (equivalente a Flask-Limiter) |
| Paginación con envelope | No incluido — se añade con `fastapi-pagination` |
| Idempotency-Key | No hay librería estándar única — se implementa como dependencia custom (registro de claves ya procesadas) |
| Modelo de datos | **SQLModel** (mismo autor que FastAPI): combina Pydantic + SQLAlchemy en una sola clase por entidad — `Product`, `Store`, `StoreProduct`... se definen una vez y sirven a la vez de modelo de BBDD y de esquema de la API, sin duplicar las entidades del modelo de datos ya diseñado |
| VAPID / Web Push | **No es responsabilidad de FastAPI** — el envío real de notificaciones vive en el módulo de scraping (el "Disparador de restock" del diagrama de componentes), usando `pywebpush`. FastAPI solo almacena y sirve las suscripciones (`push_endpoint`, `push_keys`); no envía nada él mismo |

**Efecto colateral útil**: la documentación interactiva de `/docs` sirve como panel de revisión provisional desde el primer día — se pueden probar `confirm`/`reject` de matching ahí mismo antes de tener construido el Panel de revisión real como frontend.

---

## Siguiente paso natural
Con el framework decidido, el paso lógico es traducir el listado de endpoints ya redactado a los modelos SQLModel + routers de FastAPI — momento en el que pasamos de diseño a implementación.

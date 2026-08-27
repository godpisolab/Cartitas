# API — Endpoints del gestor (panel de revisión / administración)

Documento separado de `api-endpoints-v1.md` a propósito: son dos audiencias distintas con dos API keys distintas (ver `api-endpoints-v1.md` sección 0 — cliente "Panel de revisión"). Todo lo de aquí requiere esa key; nada de este documento es alcanzable desde el frontend público.

Cubre las tres áreas de trabajo del gestor: **matching** (la cola de revisión + sus herramientas), **administración de productos canónicos**, y **administración de tiendas**. Incluye dos piezas que ya existían como función de Python (`matcher.find_missing_canonical_candidates()`, `dispatcher.query_store()`) escritas con la intención explícita de tener un endpoint algún día, y que hasta ahora no lo tenían.

---

## 0. Auth

Una sola key, un solo scope amplio de administración (`admin:*`) — no hay distintos niveles de gestor en v1 (solo tú lo usas). Si en el futuro hay varias personas con distinto nivel de confianza, ese es el momento de partir `admin:*` en scopes más finos (`admin:matching`, `admin:products`, `admin:stores`), no antes.

---

## 1. Matching

### `GET /matches` *(renombrado, decidido y ya aplicado en `api-endpoints-v1.md`)*

Antes `/matches/pending` — renombrado porque el endpoint también devuelve `status=confirmed` (una cola de "pendientes" no debería poder listar lo ya resuelto). Mismos query params y misma forma de respuesta que en `api-endpoints-v1.md` sección 6, con una adición:

| Parámetro | Tipo | Default | Novedad |
|---|---|---|---|
| `status` | `needsReview` \| `unmatched` \| `confirmed` \| `all` | `needsReview` | **`confirmed` ahora es un valor válido** — antes `all` excluía confirmados sin ninguna forma de pedirlos explícitamente |

Cuando `status=confirmed`, el campo `candidates` de la respuesta viene vacío (no se calcula top-3 para algo ya resuelto — sería trabajo de BBDD desperdiciado) y se añaden `productId` y `matchConfidence` (que si no, no tendrían valor):

```json
{
  "storeProductId": 4821,
  "store": { "id": 12, "name": "Cardzone" },
  "rawName": "One Piece TCG OP16 Booster Box (EN)",
  "matchStatus": "confirmed",
  "productId": 301,
  "matchConfidence": 0.75,
  "candidates": []
}
```

### `POST /matches/{storeProductId}/confirm`, `POST /matches/{storeProductId}/reject`

Sin cambios respecto a `api-endpoints-v1.md` — se listan aquí solo para que este documento sea el punto de entrada completo del gestor a la sección de matching, sin tener que saltar entre los dos ficheros.

### `POST /matches/{storeProductId}/reopen` *(nuevo)*

Deshace una confirmación equivocada. Parte de un `storeProduct` con `matchStatus = confirmed`, a diferencia de `reject` (que parte de uno pendiente).

**Sin body.**

**Efecto:** `matchStatus = needsReview`, `productId = null`, `matchConfidence = null`, **`reviewedAt = null`, `reviewedReason = null`** — se limpia también el rechazo (y su motivo, si lo hubiera) si lo hubiera habido antes (reabrir es "vuelve a estar activamente en revisión", el estado contrario a "ya se miró y no había nada").

**Respuesta:** `200` con el `storeProduct` actualizado. `404` si no existe. `409` si `matchStatus` no era `confirmed` (no tiene sentido "reabrir" algo que no estaba cerrado — para eso ya está `reject`).

### `GET /matches/missing-candidates` *(nuevo — expone `matcher.find_missing_canonical_candidates()`, C.1)*

**Query params:** `minStores` (int, default `2` — mismo default que la función de Python).

**Respuesta `200`:**
```json
{
  "data": [
    { "productType": "BOOSTER_BOX", "setCode": "OP17", "mainSet": "OP17", "language": "EN", "storeCount": 6 },
    { "productType": "STARTER_DECK", "setCode": "ST37", "mainSet": null, "language": "JP", "storeCount": 3 }
  ]
}
```

Agrupa y comprueba existencia de candidato por `setCode`, no por `mainSet` (2026-08-27): Double Pack/Illustration Box tienen `setCode` propio pero `mainSet` NULL en sus canónicos -- agrupar por `mainSet` generaba falsos positivos (productos que ya existían). `mainSet` se sigue devolviendo, solo para prellenar el formulario de alta cuando aplica (familia OP). Además, si no hay candidato en la categoría derivada pero SÍ existe un canónico con ese `setCode` exacto en OTRA categoría, tampoco aparece como sugerencia (caso real: "PRB02 Booster Box" sin la palabra "Premium" se clasifica como BOOSTER_BOX, pero el canónico vive en premium-collection).

Es el input natural de `POST /products` (sección 2) — el panel puede mostrar esta lista con un botón "crear canónico" por fila, prellenando `productType`/`setCode`/`mainSet`/`language` en el formulario de alta. Sin filtro de paginación: en la práctica el volumen es bajo (agrupado por combinación, no por fila), y forzar paginación aquí sería complejidad sin necesidad real.

---

## 2. Administración de productos canónicos

### `POST /products`

Sin cambios respecto a `api-endpoints-v1.md`.

### `PATCH /products/{id}` *(nuevo)*

Edición parcial — cualquier subconjunto de campos editables, pensado tanto para marcar/desmarcar caliente como para corregir errores de siembra (`nameCanonical` mal escrito, `imageUrl` roto):

**Body** (todos los campos opcionales):
```json
{ "isHot": true, "hotUntil": "2026-09-25", "nameCanonical": null, "imageUrl": null }
```

**Respuesta `200`** con el producto actualizado. `404` si no existe. `409` si el `nameCanonical` nuevo colisiona con otro producto ya existente del mismo `gameId` (misma regla de unicidad que `POST /products`).

No incluye `categoryId`/`gameId`/`setCode` como editables en v1 — cambiar la categoría de un producto ya confirmado en `store_product` tendría efectos en cascada sobre el matching (el `categoryId` es la clave con la que `matcher.py` busca candidatos) que no vale la pena resolver sin un caso real que lo pida.

---

## 3. Administración de tiendas

### `GET /stores/{id}`

Sin cambios respecto a `api-endpoints-v1.md` — ya incluía los campos dinámicos de scraping (`lastScrapedAt`, `crawlDelaySeconds`, `disallowed`, `consecutiveFailures`, `backoffUntil`).

### `PATCH /stores/{id}` *(nuevo)*

**Body:**
```json
{ "sitemapUrl": "https://cardzone.es/sitemap.xml", "active": false }
```

> ⚠️ **ALERTA DE DISEÑO — `active` no hace nada todavía:** revisé el código y `store.active` no se lee en ningún sitio de `dispatcher.py`/`run_all_stores()` — es una columna del esquema que hoy no tiene ningún efecto. Exponer `active` aquí sin más daría al gestor la falsa sensación de que desactivar una tienda la excluye del barrido, cuando en realidad seguiría scrapeándose igual. Dos formas de resolverlo, tu decisión:
> 1. Cablear `active` de verdad en `run_all_stores()` (excluir tiendas con `active=false` antes de lanzar el `ThreadPoolExecutor`) como parte de este mismo trabajo, y entonces sí incluir el campo en el `PATCH`.
> 2. Dejar `active` fuera del `PATCH` en esta primera versión (solo `sitemapUrl` editable) hasta que se cablee — evita construir un control que no controla nada.
>
> Recomiendo la opción 1 porque es poco esfuerzo (una condición extra al construir `runnable_stores` en `dispatcher.py`, mismo sitio donde ya se filtra por `backoff_until`) y cierra el hueco de raíz en vez de aplazarlo otra vez.

`sitemapUrl` sí se puede exponer sin cambios adicionales — `sitemap_poller.py` ya lo lee de BBDD tal cual, solo hacía falta una forma de escribirlo que no fuera SQL a mano.

### `POST /stores/{id}/scrape` *(nuevo — expone `dispatcher.query_store()`)*

Dispara un scraping puntual de una sola tienda, sin esperar al barrido diario.

**Sin body.**

**Respuesta `200`:**
```json
{ "status": "ok", "productsFound": 34, "elapsedSeconds": 4.2, "error": null }
```
`status` es literalmente el de `StoreQueryResult` (`ok` \| `empty` \| `timeout` \| `error` \| `circuit_open`) — se devuelve tal cual, sin reinterpretarlo, tal como ya prevé el propio docstring de `query_store()`.

> **Nota operativa, no una alerta de diseño:** esta petición puede tardar hasta `STORE_TIMEOUT` (90s) en responder si la tienda va lenta — es una llamada HTTP síncrona de larga duración. Para v1 es aceptable (el panel de revisión sois vosotros, no tráfico público), pero el frontend del panel debe mostrar un estado de carga acorde, no asumir que esto responde en milisegundos como el resto de la API.

Este endpoint **no** persiste el resultado en `store_product`/`price_history` — es una consulta puntual de diagnóstico ("¿sigue fallando esta tienda ahora mismo?"), no un barrido que se guarde. Si en el futuro hiciera falta que también persista, sería una decisión aparte (probablemente reutilizando `persistence._save_one_store()` igual que ya hace `refresh_hot_products`).

---

## Resumen de piezas de esquema/código pendientes que este documento deja pedidas

**Decidido:**
1. ~~Renombrar `GET /matches/pending` → `GET /matches`~~ — hecho, ambos documentos ya son consistentes.
2. ~~`store_product.reviewed_at`~~ — hecho (2026-08-27): `ALTER TABLE` directo sobre `schema-postgresql-app-tcg.sql` (columna + comentario), aplicado a `cartitas` y `cartitas_test`. Sin Alembic, como decisión ya tomada para todo el proyecto en esta fase.
3. ~~Cablear `store.active` en `dispatcher.run_all_stores()`~~ — hecho (2026-08-27, elegida la opción 1 de la alerta de arriba): una tienda con `active = false` se salta igual que una en backoff, antes de lanzar el `ThreadPoolExecutor`. Ver `store_monitor/README.md` y `store_monitor/tests/test_dispatcher.py::TestStoreActive`.

**Aplazado explícitamente (no resuelto):**
4. `POST /stores/{id}/scrape` -- ninguno de los dos documentos resolvía CÓMO `api/` (sin las dependencias de scraping, ver `estandares-implementacion-api.md` sección 1) puede invocar `dispatcher.query_store()` de `store_monitor/` sin volver a mezclar los dos servicios. Decidido (2026-08-27) aplazar este endpoint concreto como su propia tarea de diseño en vez de forzar una solución -- el resto de esta sección (matching, `PATCH`/`POST` de productos y tiendas) sí está implementado.

## Siguiente paso natural

**Hecho (2026-08-27):** matching completo (`GET /matches` con los 4 valores de `status`, `confirm`/`reject`/`reopen`, `missing-candidates`), `POST`/`PATCH /products`, y `GET`/`PATCH /stores/{id}` -- en `routers/matches.py`, `routers/products.py` y `routers/stores.py` respectivamente, todos detrás de `Depends(require_scope("admin:*"))` (scope único de administración, tal como pedía la sección 0 -- no se partió en scopes más finos, no hay todavía varias personas con distinto nivel de confianza). Pendiente: `POST /stores/{id}/scrape` (punto 4 de arriba).

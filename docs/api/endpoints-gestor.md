# API — Endpoints del gestor (panel de revisión / administración)

Documento separado de `docs/api/endpoints-v1.md` a propósito: son dos audiencias distintas con dos API keys distintas (ver `docs/api/endpoints-v1.md` sección 0 — cliente "Panel de revisión"). Todo lo de aquí requiere esa key; nada de este documento es alcanzable desde el frontend público.

Cubre las tres áreas de trabajo del gestor: **matching** (la cola de revisión + sus herramientas), **administración de productos canónicos**, y **administración de tiendas**. Incluye dos piezas que ya existían como función de Python (`matcher.find_missing_canonical_candidates()`, `dispatcher.query_store()`) escritas con la intención explícita de tener un endpoint algún día, y que hasta ahora no lo tenían.

---

## 0. Auth

Una sola key, un solo scope amplio de administración (`admin:*`) — no hay distintos niveles de gestor en v1 (solo tú lo usas). Si en el futuro hay varias personas con distinto nivel de confianza, ese es el momento de partir `admin:*` en scopes más finos (`admin:matching`, `admin:products`, `admin:stores`), no antes.

---

## 1. Matching

### `GET /matches` *(renombrado, decidido y ya aplicado en `docs/api/endpoints-v1.md`)*

Antes `/matches/pending` — renombrado porque el endpoint también devuelve `status=confirmed` (una cola de "pendientes" no debería poder listar lo ya resuelto). Mismos query params y misma forma de respuesta que en `docs/api/endpoints-v1.md` sección 6, con una adición:

| Parámetro | Tipo | Default | Novedad |
|---|---|---|---|
| `status` | `needsReview` \| `unmatched` \| `confirmed` \| `all` | `needsReview` | **`confirmed` ahora es un valor válido** — antes `all` excluía confirmados sin ninguna forma de pedirlos explícitamente |

Cuando `status=confirmed`, el campo `candidates` de la respuesta viene vacío (no se calcula top-3 para algo ya resuelto — sería trabajo de BBDD desperdiciado) y se añaden `productId` y `matchConfidence` (que si no, no tendrían valor):

```json
{
  "storeProductId": 4821,
  "store": { "id": 12, "name": "Cardzone" },
  "storeUrl": "https://cardzone.example.com/product/op16-booster-box-en",
  "rawName": "One Piece TCG OP16 Booster Box (EN)",
  "matchStatus": "confirmed",
  "productId": 301,
  "matchConfidence": 0.75,
  "candidates": []
}
```

### `POST /matches/{storeProductId}/confirm`, `POST /matches/{storeProductId}/reject`

Sin cambios respecto a `docs/api/endpoints-v1.md` — se listan aquí solo para que este documento sea el punto de entrada completo del gestor a la sección de matching, sin tener que saltar entre los dos ficheros.

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
    { "productType": "ONE_PIECE", "setCode": "OP17", "mainSet": "OP17", "language": "EN", "packaging": "display", "storeCount": 6 },
    { "productType": "STARTER_DECK", "setCode": "ST37", "mainSet": null, "language": "JP", "packaging": "sobre", "storeCount": 3 }
  ]
}
```

Agrupa y comprueba existencia de candidato por `setCode` + `packaging`, no por `mainSet`: Double Pack tiene `setCode` propio pero `mainSet` NULL en sus canónicos -- agrupar por `mainSet` generaba falsos positivos (productos que ya existían). `mainSet` se sigue devolviendo, solo para prellenar el formulario de alta cuando aplica (familia OP/Double Pack). `packaging` entra en la clave de agrupación (Recognition Pipeline, docs/propuestas/guia_nuevo_matcher.md) porque `ONE_PIECE`/`EXTRA_BOOSTER`/`PREMIUM_BOOSTER_BOX` unifican sobre/display/case en una única categoría -- sin él, un canónico 'sobre' ya sembrado escondería que falta la variante 'display' del mismo `setCode` (precio completamente distinto). Además, si no hay candidato en la categoría derivada pero SÍ existe un canónico con ese `setCode` exacto (y mismo `packaging`) en OTRA categoría, tampoco aparece como sugerencia (caso real: un canónico `PRB-NN` sembrado por error en `premium-card-collection` en vez de `premium-booster-box`).

Es el input natural de `POST /products` (sección 2) — el panel puede mostrar esta lista con un botón "crear canónico" por fila, prellenando `productType`/`setCode`/`mainSet`/`language`/`packaging` en el formulario de alta. Sin filtro de paginación: en la práctica el volumen es bajo (agrupado por combinación, no por fila), y forzar paginación aquí sería complejidad sin necesidad real.

---

## 2. Administración de productos canónicos

### `POST /products`

Sin cambios respecto a `docs/api/endpoints-v1.md`.

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

Sin cambios respecto a `docs/api/endpoints-v1.md` — ya incluía los campos dinámicos de scraping (`lastScrapedAt`, `crawlDelaySeconds`, `disallowed`, `consecutiveFailures`, `backoffUntil`).

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

### Scraping manual desde el panel *(resuelto 2026-08-29, ver docs/propuestas/propuesta-scraping-manual-panel.md)*

La forma final NO es un endpoint JSON síncrono en esta API pública, sino una
acción del panel HTML admin (`POST /admin/stores/{id}/scrape`, botón "Lanzar
scrape ahora" en `GET /admin/stores/{id}`) que llama por HTTP a un servicio
interno nuevo en `store_monitor/` (`jobs_api.py`, puerto 8001 por defecto,
`api/services/jobs.py` es el cliente) — así resuelve el "CÓMO" que quedaba
aplazado: `api/` sigue sin importar nada de `store_monitor/`
(`cloudscraper`/`pybreaker`), le habla solo por HTTP. Ese mismo servicio
también expone los tres jobs de `scheduler.py` (barrido diario / refresco de
calientes / polling de sitemap), disparables a mano desde `GET /admin/jobs`,
con historial de ejecuciones en la misma página.

**Asíncrono, no bloqueante:** el POST crea una fila `scrape_run` (con su
propio log de ejecución en fichero, `logs/{run_id}.log` -- reemplaza los
`print()` sueltos que tenía el scraper) y lanza el scrape en un hilo de
fondo del proceso de `store_monitor/`, devolviendo `run_id` al momento; el
panel sondea `GET /admin/jobs/runs/{run_id}` vía htmx (`hx-trigger="every
2s"`) hasta que `status` deja de ser `running`. Mientras corre, esa misma
respuesta lleva el progreso: determinado (`storesDone`/`storesTotal`) para
un barrido completo, y para una tienda suelta una estimación de página
(`currentPage`/`estimatedTotalPages`, cacheada en
`store.last_known_page_count` del último scrape real) -- ver
`docs/propuestas/propuesta-scraping-manual-panel.md` puntos 2 y 4 para el
razonamiento completo.

**Persistir o no, a elección de quien dispara (ampliación 2026-08-29):**
`POST /jobs/store/{label}?persist=true|false` en el servicio interno
(`persist=false` por defecto, checkbox sin marcar en el panel). Con
`persist=false` es una consulta de solo diagnóstico -- nada se escribe en
`store_product`/`price_history`, tal como el diseño original de este
documento ya preveía. Con `persist=true` reutiliza el mismo camino que el
barrido diario (`persistence.persist_scrape_results` -> notificaciones de
restock -> `matcher.run_matching`), así que el resultado sí queda guardado y
entra en la cola de matching.

**En cualquiera de los dos casos, el resultado se ve:** los productos
encontrados (nombre, variante, precio, stock, enlace a la tienda) se
guardan en memoria del proceso (`store_monitor/run_results.py`, no en BBDD
salvo que `persist=true`) y aparecen en una tabla bajo el run una vez
termina -- para que un disparo sin persistir sirva de algo más que "sigue
funcionando o no". Se pierden si el proceso de `store_monitor/` se
reinicia, aceptable para algo pensado como vista previa desechable.

---

## Resumen de piezas de esquema/código pendientes que este documento deja pedidas

**Decidido:**
1. ~~Renombrar `GET /matches/pending` → `GET /matches`~~ — hecho, ambos documentos ya son consistentes.
2. ~~`store_product.reviewed_at`~~ — hecho (2026-08-27): `ALTER TABLE` directo sobre `schema-postgresql-app-tcg.sql` (columna + comentario), aplicado a `cartitas` y `cartitas_test`. Sin Alembic, como decisión ya tomada para todo el proyecto en esta fase.
3. ~~Cablear `store.active` en `dispatcher.run_all_stores()`~~ — hecho (2026-08-27, elegida la opción 1 de la alerta de arriba): una tienda con `active = false` se salta igual que una en backoff, antes de lanzar el `ThreadPoolExecutor`. Ver `store_monitor/README.md` y `store_monitor/tests/test_dispatcher.py::TestStoreActive`.

4. ~~`POST /stores/{id}/scrape`~~ — resuelto (2026-08-29, ver docs/propuestas/propuesta-scraping-manual-panel.md): no como endpoint JSON síncrono en esta API, sino como acción del panel HTML admin sobre un servicio HTTP interno nuevo en `store_monitor/` (`jobs_api.py`) -- ver sección de arriba para el diseño final.

## Siguiente paso natural

**Hecho (2026-08-27):** matching completo (`GET /matches` con los 4 valores de `status`, `confirm`/`reject`/`reopen`, `missing-candidates`), `POST`/`PATCH /products`, y `GET`/`PATCH /stores/{id}` -- en `routers/matches.py`, `routers/products.py` y `routers/stores.py` respectivamente, todos detrás de `Depends(require_scope("admin:*"))` (scope único de administración, tal como pedía la sección 0 -- no se partió en scopes más finos, no hay todavía varias personas con distinto nivel de confianza).

**Hecho (2026-08-29):** el punto 4 de arriba (scraping manual desde el panel) -- ver esa sección para el diseño final. Nada pendiente de esta lista por ahora.

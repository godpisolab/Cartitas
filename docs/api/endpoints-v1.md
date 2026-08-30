# Endpoints v1 — API completa (fusión)

Fusiona la cobertura completa del primer borrador (productos, ofertas, tiendas, catálogo de filtros, suscripciones, panel de revisión) con el nivel de detalle de payload/respuesta y las alertas de diseño encontradas al especificarlo. Convención de nombres: **`camelCase`** en todo el JSON (cuerpos y query params), consistente con `docs/api/estandares.md` sección 5 — el borrador anterior usaba `snake_case`, corregido aquí.

> **Notación de este documento:** los bloques marcados `⚠️ ALERTA DE DISEÑO` son cosas que encontré al bajar al detalle y que no estaban resueltas en ninguno de los dos borradores anteriores — necesitan tu confirmación antes de implementarse, no son decisiones que haya tomado por mi cuenta.

---

## 0. Modelo de autenticación

Toda petición — lectura o escritura — requiere `Authorization: Bearer <apiKey>`. No es autenticación de usuario final (sigue sin login); identifica **qué aplicación cliente** llama, no quién es la persona.

| Cliente | Lectura | Escritura permitida |
|---|---|---|
| Frontend público | Todo lo de solo lectura | `POST /subscriptions`, `DELETE /subscriptions/{id}` |
| Panel de revisión | Todo lo de solo lectura + cola de matching | `POST /matches/{id}/confirm`, `POST /matches/{id}/reject`, `POST /products` |
| App futura | A decidir cuando exista | A decidir cuando exista |

API key estática por cliente, sin expiración ni flujo de login — lo mínimo que cumple "todo autenticado". Rotación/expiración quedan como mejora futura sin romper el diseño (un endpoint de emisión de tokens se puede añadir después sin tocar esto).

Errores de auth: `401` (falta la cabecera o la key no existe), `403` (key válida pero sin el scope necesario para esa acción) — según la tabla ya fijada en `docs/api/estandares.md`.

---

## 1. Productos

### `GET /products`

**Auth:** cualquier cliente, lectura.

**Query params:**

| Parámetro | Tipo | Descripción |
|---|---|---|
| `q` | string | texto libre sobre `nameCanonical` (`pg_trgm`) |
| `game` | string | slug (`one-piece`, `pokemon`) |
| `category` | string | slug (`one-piece`, `starter-deck`...) |
| `setCode` | string | ej. `OP16` |
| `language` | `EN` \| `JP` \| `ES` | |
| `minPrice`, `maxPrice` | number | sobre el precio mínimo actual entre tiendas |
| `isHot` | bool | solo productos marcados calientes (E.2) |
| `page`, `limit` | int | default `1`, `20` |

**Respuesta `200`:**
```json
{
  "data": [
    {
      "id": 301,
      "nameCanonical": "Booster Box: The Time of Battle OP-16 EN",
      "game": "one-piece",
      "category": "one-piece",
      "setCode": "OP16",
      "language": "EN",
      "packaging": "display",
      "minPrice": 109.90,
      "storeCount": 6,
      "anyInStock": true
    }
  ],
  "meta": { "page": 1, "limit": 20, "total": 842 }
}
```

Solo cuentan `storeProduct` con `matchStatus = confirmed` para `minPrice`/`storeCount`/`anyInStock` — un `needsReview` no debe aparecer como si el vínculo ya estuviera confirmado.

**Corregido (2026-08-27):** `minPrice` puede ser `null` -- un `storeProduct` confirmado con `currentPrice` sin parsear (preventa, o el scraper no pudo leer el precio esa pasada) hace que `MIN()` devuelva `NULL`. Bug real encontrado en producción: la implementación original asumía `minPrice` siempre numérico y reventaba el endpoint entero con `500` en cuanto existía un caso así -- corregido en `services/products.py`/`schemas/products.py` (`ProductSummary.min_price: float | None`).

**`packaging` (Recognition Pipeline, docs/propuestas/guia_nuevo_matcher.md):** `"sobre"` | `"display"` | `"case"` | `null`. Sustituye a la antigua separación caja/sobre/case POR CATEGORÍA (antes `booster-box`/`booster-pack`/`booster-case` eran tres categorías distintas) -- ahora conviven en `category: "one-piece"` (igual para `extra-booster`/`premium-booster-box`), distinguidas por este campo. `null` para família sin esa dimensión (Illustration Box, Playmat, Sleeves, Devil Fruits Collection, Premium Card Collection).

### `GET /products/{id}`

**Respuesta `200`:**
```json
{
  "id": 301,
  "nameCanonical": "Booster Box: The Time of Battle OP-16 EN",
  "game": "one-piece",
  "category": "one-piece",
  "setCode": "OP16",
  "mainSet": "OP16",
  "language": "EN",
  "packaging": "display",
  "listings": [
    {
      "storeId": 12,
      "storeName": "Cardzone",
      "price": 109.90,
      "stockStatus": "disponible",
      "url": "https://cardzone.es/products/op16-booster-box",
      "lastCheckedAt": "2026-08-27T04:03:00Z"
    }
  ]
}
```

`listings` ordenado de más barato a más caro, siempre. Solo incluye `storeProduct` con `matchStatus = confirmed`.

**Resuelto (2026-08-27):** si un `id` existe en `product` pero no tiene ningún `storeProduct` confirmado todavía (recién sembrado, esperando matching), la respuesta es `404` — desde fuera, "sin ninguna tienda confirmada" es indistinguible de "no existe", y no tiene sentido enseñar una ficha vacía.

### `GET /products/{id}/price-history`

**Query params:** `storeId` (opcional — si se omite, agrega el precio mínimo entre tiendas por día; si se pasa, la curva de esa tienda concreta).

**Respuesta `200`:**
```json
{
  "productId": 301,
  "storeId": null,
  "series": [
    { "date": "2026-08-20", "minPrice": 115.00, "stockStatus": "disponible" },
    { "date": "2026-08-27", "minPrice": 109.90, "stockStatus": "disponible" }
  ]
}
```

Cuando `storeId` es `null`, `stockStatus` del día es `"disponible"` si **alguna** tienda lo tenía disponible ese día, no un agregado más fino — no hay una noción razonable de "stock medio" entre tiendas.

---

## 2. Ofertas

### `GET /deals`

Ranking de mejores ofertas activas — alimenta la home ("chollómetro" del diseño funcional).

**Query params:** `game`, `category`, `limit` (default `20`).

**Respuesta `200`:**
```json
{
  "data": [
    {
      "productId": 301,
      "nameCanonical": "Booster Box: The Time of Battle OP-16 EN",
      "currentMinPrice": 99.90,
      "previousMinPrice": 119.90,
      "dropPercentage": 16.7,
      "comparedTo": "2026-08-20",
      "storeName": "Cardzone"
    }
  ]
}
```

> ⚠️ **ALERTA DE DISEÑO — asunción a confirmar:** el ranking compara el `minPrice` de hoy contra el de hace **7 días** (ventana fija propuesta, configurable como constante del backend, no como query param en v1). Un producto sin `priceHistory` de hace 7 días (lanzamiento reciente) simplemente no puede entrar en el ranking — no se compara contra "nunca" ni se infla artificialmente. Y solo entran productos con `anyInStock = true` en este momento: una bajada de precio en algo agotado no es una oferta accionable.

---

## 2.1 Feed de restocks recientes

### `GET /restock-events`

Nivel 1 del diseño funcional ("últimas 24h: qué ha vuelto a stock") — feed público, consumible/compartible, no confundir con la cola de matching (que es del gestor).

**Query params:** `game`, `category`, `hours` (int, default `24`), `limit` (int, default `50`).

**Respuesta `200`:**
```json
{
  "data": [
    {
      "productId": 301,
      "nameCanonical": "Booster Box: The Time of Battle OP-16 EN",
      "storeName": "Cardzone",
      "price": 109.90,
      "detectedAt": "2026-08-27T04:03:00Z"
    }
  ]
}
```

Fuente: `restock_event` (join con `store_product`/`product`/`store`), ordenado por `detectedAt` descendente. Solo eventos con `product_id` no nulo (los únicos que existen, ver B.2 — un restock sin match confirmado nunca genera fila aquí, así que no hace falta filtrar nada adicional).

---

## 3. Tiendas

### `GET /stores`

**Respuesta `200`:**
```json
{
  "data": [
    { "id": 12, "name": "Cardzone", "websiteUrl": "https://cardzone.es", "platform": "shopify", "active": true }
  ]
}
```

### `GET /stores/{id}`

Pensado para el panel de revisión/depuración, no para el frontend público — incluye los campos dinámicos de scraping respetuoso (A.2/A.3):

```json
{
  "id": 12,
  "name": "Cardzone",
  "websiteUrl": "https://cardzone.es",
  "platform": "shopify",
  "active": true,
  "lastScrapedAt": "2026-08-27T04:00:12Z",
  "crawlDelaySeconds": null,
  "disallowed": false,
  "consecutiveFailures": 0,
  "backoffUntil": null
}
```

---

## 4. Catálogo de filtros

### `GET /games`
```json
{ "data": [ { "id": 1, "name": "One Piece", "slug": "one-piece" }, { "id": 2, "name": "Pokémon", "slug": "pokemon" } ] }
```

### `GET /categories`
Árbol anidado (padre → hijos), listo para poblar un filtro de dos niveles sin construir el árbol en el cliente:
```json
{
  "data": [
    {
      "id": 1, "name": "Sellado", "slug": "sellado",
      "children": [
        { "id": 3, "name": "One Piece", "slug": "one-piece" },
        { "id": 4, "name": "Starter Deck", "slug": "starter-deck" }
      ]
    },
    {
      "id": 2, "name": "Accesorios", "slug": "accesorios",
      "children": [ { "id": 9, "name": "Playmat", "slug": "playmat" } ]
    }
  ]
}
```

`Lote de cartas`/`Otros`/`Promo Card`/`Mystery Pack`/`Dice / Accessory` nunca aparecen aquí — no están sembrados en `category` a propósito (Recognition Pipeline, Fase 0: ninguno tiene un canónico de producto sellado razonable con el que comparar precio).

**Taxonomía de categorías-hoja (10, Recognition Pipeline):** `one-piece` (funde las antiguas `booster-box`/`booster-pack`/`booster-case` — la distinción caja/sobre/case pasó a ser el campo `packaging`, ver sección 1), `extra-booster`, `premium-booster-box`, `premium-card-collection` (la antigua `premium-collection` se separó en estas dos: son productos distintos, no variantes de empaquetado del mismo), `starter-deck` (funde la antigua `learn-deck`), `illustration-box`, `double-pack`, `devil-fruits-collection`, `sleeves` (nueva), `playmat`.

---

## 5. Suscripciones de restock

### `POST /subscriptions`

**Body:**
```json
{
  "productId": 301,
  "storeId": null,
  "pushEndpoint": "https://fcm.googleapis.com/fcm/send/...",
  "pushKeys": { "p256dh": "...", "auth": "..." }
}
```

`storeId: null` = cualquier tienda (único comportamiento de v1). Requiere cabecera `Idempotency-Key` (un reintento de red tras un 201 con éxito no debe crear una segunda suscripción).

**Respuesta `201`:**
```json
{ "id": 8842, "productId": 301, "storeId": null }
```

**`409`** si ya existe una suscripción idéntica (`UNIQUE(productId, storeId, pushEndpoint)` del esquema) — consistente con la tabla de códigos HTTP ya fijada, que nombra explícitamente "suscripción duplicada" como ejemplo de `409`.

**`422`** si `productId` no existe.

### `DELETE /subscriptions/{id}`

**Query param obligatorio:** `pushEndpoint`.

**Resuelto (no queda como alerta):** `restockSubscription.id` es un `SERIAL` correlativo y adivinable — sin ninguna comprobación adicional, cualquiera podría probar IDs consecutivos y borrar suscripciones ajenas. En vez de migrar a UUID, el `pushEndpoint` (ya es un secreto largo que generó el propio navegador) actúa como prueba de propiedad: el borrado solo procede si coincide con el que creó esa fila. `403` si no coincide, `404` si el `id` no existe.

### `GET /subscriptions?pushEndpoint=X`

Lista las suscripciones activas de un dispositivo, identificado por su `pushEndpoint` — permite el "productos que sigues" del frontend sin cuenta de usuario.

**Respuesta `200`:**
```json
{ "data": [ { "id": 8842, "productId": 301, "storeId": null, "createdAt": "2026-08-27T10:00:00Z" } ] }
```

---

## 6. Panel de revisión (matching)

### `GET /matches`

**Renombrado (2026-08-27):** antes `/matches/pending` — dejó de tener sentido en cuanto el endpoint también puede devolver `status=confirmed` (una cola de "pendientes" no debería poder listar lo ya resuelto). Ver `docs/api/endpoints-gestor.md` sección 1 para el uso de `status=confirmed` (auditar matches ya confirmados) y las herramientas adicionales de esa cola (`reopen`, `missing-candidates`).

**Query params:**

| Parámetro | Tipo | Descripción |
|---|---|---|
| `status` | `needsReview` \| `unmatched` \| `confirmed` \| `all` | default `needsReview` — `notApplicable` nunca aparece bajo ningún valor |
| `storeId` | int | filtra por tienda |
| `minSimilarity`, `maxSimilarity` | number | sobre el score del **mejor candidato calculado en caliente**, no sobre `matchConfidence` (ver alerta abajo) |
| `includeReviewed` | bool | default `false` — ver `reviewedAt` más abajo |
| `page`, `limit` | int | |

**Respuesta `200`:**
```json
{
  "data": [
    {
      "storeProductId": 4821,
      "store": { "id": 12, "name": "Cardzone" },
      "storeUrl": "https://cardzone.example.com/product/op16-booster-box-en",
      "rawName": "One Piece TCG OP16 Booster Box (EN)",
      "rawVariant": null,
      "currentPrice": 119.90,
      "stockStatus": "disponible",
      "matchStatus": "needsReview",
      "reviewedAt": null,
      "candidates": [
        { "productId": 301, "nameCanonical": "Booster Box: The Time of Battle OP-16 EN", "similarity": 0.58 },
        { "productId": 302, "nameCanonical": "Booster Pack: The Time of Battle OP-16 EN", "similarity": 0.41 }
      ]
    }
  ],
  "meta": { "page": 1, "limit": 50, "total": 137 }
}
```

> ⚠️ **ALERTA DE DISEÑO #1 — `minSimilarity`/`maxSimilarity` no puede filtrar sobre `matchConfidence`:** `matcher._evaluate()` solo rellena `match_confidence` cuando el resultado es `confirmed` — para todo lo que aparece en esta cola (`needsReview`/`unmatched`), esa columna es siempre `NULL` por diseño. Filtrar/priorizar por confianza solo tiene sentido contra el score del **mejor candidato calculado en vivo** (la misma consulta `ORDER BY similarity(...) DESC LIMIT 3` que ya usa el top-3), no contra una columna de la tabla. Lo dejo así especificado en vez de replicar el error del borrador original.

> ⚠️ **ALERTA DE DISEÑO #2 — resuelta aquí, necesita cambio de esquema:** `matcher.run_matching()` reevalúa TODO lo no-`confirmed` en cada pasada del scheduler (decisión de C.4, correcta en general). Sin nada más, un `reject` de hoy se pierde en el barrido de mañana — el humano rechaza, el matcher lo vuelve a poner en la cola sin memoria de que ya se miró. Propuesta concreta:
> - **Añadir `store_product.reviewed_at TIMESTAMPTZ NULL`** al esquema — independiente de `match_status`, `run_matching()` no la toca nunca.
> - `POST /matches/{id}/reject` (ver abajo) la rellena.
> - `GET /matches/pending` excluye por defecto filas con `reviewedAt` dentro de los últimos **14 días** (constante de backend, no query param en v1); `includeReviewed=true` las muestra igual.
> - **Limitación aceptada, no resuelta del todo:** si aparece un candidato genuinamente mejor durante esos 14 días, la fila sigue oculta hasta que expire — no hay mecanismo para "despertarla" antes. Aceptable para v1; se puede afinar con datos reales de cuántos `reject` ocurren en la práctica.

### `POST /matches/{storeProductId}/confirm`

**Body:**
```json
{ "productId": 301 }
```

**Efecto:** `matchStatus = confirmed`, `productId` rellenado. `matchConfidence` se guarda solo si `productId` coincide con uno de los `candidates` vigentes en el momento de confirmar (permite distinguir después "el algoritmo ya acertaba" de "elección manual pura", útil para calibrar los umbrales de C.2 con datos reales); si es una elección manual sin relación con las sugerencias, `matchConfidence = null`.

**Respuesta:** `200` con el `storeProduct` actualizado. `404` si `storeProductId` no existe, `422` si `productId` no existe. Idempotente por naturaleza — no necesita `Idempotency-Key`.

### `POST /matches/{storeProductId}/reject`

**Body:**
```json
{ "markAs": "unmatched", "reason": "Es un accesorio, no una caja -- classify_product() lo confundió por el nombre" }
```

`markAs`: `needsReview` \| `unmatched`. `reason` opcional, texto libre — no se usa para nada automático, solo queda como nota para quien revise después (`store_product.reviewed_reason`, hecho 2026-08-27 — hasta entonces el campo se aceptaba en el body pero se descartaba en silencio, ver `docs/plan-cierre-panel-gestor.md` sección 1.2). `reopen` la limpia a `null` igual que `reviewedAt`.

**Efecto:** `matchStatus = markAs`, `reviewedAt = now()`. `productId` se pone a `null` si no lo estaba ya.

**Respuesta:** `200` con el `storeProduct` actualizado.

### `POST /products` *(escritura de administración)*

Crea un producto canónico a mano — para sembrar un set recién lanzado, o cuando el panel determina que un `storeProduct` no encaja con nada existente y hace falta un canónico nuevo (cierra el círculo con `matcher.find_missing_canonical_candidates()`, C.1).

**Body:**
```json
{
  "gameId": 1,
  "categoryId": 3,
  "setCode": "OP17",
  "mainSet": "OP17",
  "language": "EN",
  "packaging": "display",
  "nameCanonical": "Booster Box: The World's Strongest Warriors OP-17 EN",
  "imageUrl": null,
  "isHot": true,
  "hotUntil": "2026-09-25"
}
```

**Respuesta `201`** con el `product` creado. `409` si ya existe un `product` con el mismo `gameId` + `nameCanonical` (evita duplicados desde el panel, mismo criterio de idempotencia que ya usa `seed_official_catalog.py`).

`packaging` (opcional): `"sobre"` \| `"display"` \| `"case"` \| `null` — obligatorio en la práctica para família que sí tiene esa dimensión (ver `docs/propuestas/guia_nuevo_matcher.md`), porque `matcher.py` lo usa para el desempate/auto-confirmado; se deja `null` para família de unidad única (Illustration Box, Playmat, Sleeves...).

---

## Resumen de cobertura

Cada funcionalidad del núcleo del diseño funcional (buscador, ficha, comparación, histórico, ranking de ofertas, feed de restocks recientes, alertas de restock) y del panel de revisión tiene endpoint propio, sin endpoints a medida de una pantalla concreta. Los endpoints exclusivos del gestor (matching completo, administración de productos/tiendas) viven en `docs/api/endpoints-gestor.md`, no aquí. Una pieza de esquema nueva que este documento deja pedida y que aún no existe en `schema-postgresql-app-tcg.sql`:

1. `store_product.reviewed_at TIMESTAMPTZ NULL` (alerta #2 de la sección de matching).

El resto de endpoints encajan en el esquema ya existente sin cambios.

## Siguiente paso natural

Con las dos alertas de diseño resueltas por escrito (la de `minSimilarity` es solo una aclaración de dónde consultar, la de `reviewedAt` sí pide una migración pequeña de esquema), esto ya se puede traducir a routers de FastAPI + modelos SQLModel. La migración de `reviewed_at` es además un buen primer caso real para decidir si merece la pena adelantar Alembic, o si sigue entrando dentro de "cambio aditivo trivial" que no necesita la herramienta todavía.

**Hecho (2026-08-27):** todos los endpoints de este documento están implementados en `api/` -- productos (buscador, ficha, histórico, alta/edición de administración), ofertas, feed de restocks recientes, tiendas (lectura + `PATCH` de administración), catálogo de filtros, suscripciones (sin `Idempotency-Key` real, ver `docs/api/estandares-implementacion.md` sección 7), y el panel de matching completo (`GET /matches` con los cuatro valores de `status`, `confirm`/`reject`/`reopen`, `missing-candidates`). `store_product.reviewed_at` ya existe en el esquema (aplicado como `ALTER TABLE` directo, sin Alembic). 140 tests, 99% de cobertura. Lo único NO implementado de la superficie completa (`docs/api/endpoints-v1.md` + `docs/api/endpoints-gestor.md`) es `POST /stores/{id}/scrape`, aplazado explícitamente por el puente de dependencias sin resolver hacia `store_monitor/` (ver `docs/api/endpoints-gestor.md`, sección 3).

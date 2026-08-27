# Frontend — Arquitectura y stack (decidido)

Documento de cierre para el equipo. Consolida las decisiones tomadas sobre el frontend de Cartitas — arquitectura, stack, seguridad — y deja explícito qué queda deliberadamente diferido para más adelante, para que nadie lo confunda con un olvido.

---

## Resumen ejecutivo

- **Dos aplicaciones separadas**, no una: frontend público y panel de gestor.
- **Frontend público → Astro.**
- **Panel de gestor → vive dentro de `api/`** (Jinja2 + htmx), no como aplicación aparte.
- Seguridad del panel en dos capas: autenticación (HTTP Basic) + restricción de red (IP allowlist).
- La API ya está preparada para una futura app móvil sin cambios estructurales, con una salvedad anotada sobre notificaciones push si resulta ser nativa.
- Estética, librería de UI para partes interactivas, y hosting quedan fuera de esta ronda — se deciden cuando toque construir esas piezas, no antes.

---

## 1. Dos aplicaciones, no una

Frontend público y panel de gestor tienen audiencias, requisitos y superficies de seguridad distintos — mismo criterio ya aplicado al separar `api/` de `store_monitor/` (`estandares_organizacion_codigo.md`). Compartir codebase por comodidad haría que las necesidades de uno condicionaran las decisiones del otro sin necesidad real.

| | Frontend público | Panel de gestor |
|---|---|---|
| Quién lo usa | Cualquier visitante, sin login | El equipo, sin más usuarios previstos por ahora |
| Requisito no negociable | SEO real, velocidad de carga, contenido compartible | Ninguno de los dos |
| Naturaleza | Contenido + búsqueda + una acción de escritura (suscribirse) | Formularios y acciones de administración |

---

## 2. Frontend público — Astro

**Decisión:** Astro, no Next.js ni SvelteKit.

**Por qué:** el sitio es mayormente contenido de lectura (ficha de producto, comparación, histórico) con interactividad puntual y acotada (filtros, botón de suscripción, gráfico) — el perfil exacto para el que Astro renderiza HTML estático por defecto y solo carga JavaScript donde se declara explícitamente ("islas"). Se revisaron patrones reales de la industria (price-trackers pequeños existentes están mayoritariamente en Next.js) y se mantiene Astro de forma consciente porque dos rasgos del proyecto pesan más que esa convención: el feed de restocks está pensado para compartirse (la velocidad de carga afecta al click-through real) y el SEO es el canal de descubrimiento principal, no un extra.

**Coste aceptado:** no existe una plantilla previa de "price tracker en Astro" de la que partir — algunas piezas se construirán apoyándose en documentación general de Astro, no en un ejemplo ya adaptado.

### 2.1 Acceso a la API — la key pública se vuelve genuinamente secreta

Las lecturas (`GET /products`, `GET /deals`, `GET /restock-events`...) se renderizan en el servidor de Astro durante SSR/SSG — la API key nunca llega al navegador. La única escritura del lado público (`POST /subscriptions`, `GET /subscriptions?pushEndpoint=`) pasa por una ruta de servidor propia de Astro (patrón *Backend-for-Frontend*) que guarda la key real y reenvía la petición — el navegador nunca la ve. Mejora sobre el diseño original de la API (que asumía la key pública como "no secreta de verdad").

### 2.2 Caché alineada con las cadencias reales del backend

No se piden datos frescos en cada visita — se regenera según con qué frecuencia cambian de verdad, ya definido en el backend:

| Contenido | Cadencia real del backend | Revalidación del frontend |
|---|---|---|
| Ficha de producto normal | Barrido diario | ~1h |
| Producto `isHot` | Refresco cada 2-4h | 15-30 min |
| Feed de restocks recientes | En el momento del barrido/refresco | 5-15 min |
| Búsqueda con filtros | Cambia por combinación | Sin pre-renderizar, SSR puro |

**Nota técnica:** el equivalente de ISR en Astro depende del adaptador de despliegue elegido — se resuelve al decidir el hosting, no antes.

### 2.3 Requisito técnico: alertas de restock

El botón "avísame" necesita un Service Worker registrado (`navigator.serviceWorker.register()` + `PushManager.subscribe()` con la clave pública VAPID) antes de poder generar lo que `POST /subscriptions` espera. Es trabajo real de frontend, no una llamada trivial — se dimensiona como tarea propia.

---

## 3. Panel de gestor — dentro de `api/`

**Decisión:** el panel no es una SPA en JavaScript aparte. Vive como páginas HTML servidas por el propio proceso de `api/` (Jinja2 para plantillas, htmx para que confirmar/rechazar/reabrir actualicen la página sin recarga completa).

**Por qué:** no hay sistema de login en el proyecto — el modelo de auth es "una key por aplicación cliente", no "una persona con contraseña". Una SPA con la key `admin:*` incrustada en el bundle de JS sería, literalmente, publicar la contraseña de administrador en código que cualquiera puede leer con las herramientas de desarrollador del navegador. Con el panel dentro de `api/`, las rutas HTML llaman a `services/matches.py`/`services/products.py` **directamente en el mismo proceso Python** — no existe ninguna petición HTTP externa que autenticar con key, así que no hay ninguna key que pueda filtrarse.

**Precedente:** no es una solución improvisada — es el mismo patrón de Django Admin / Rails ActiveAdmin (panel server-rendered en el mismo proceso que la aplicación, con su propio mecanismo de auth de persona, separado de cualquier API key que la aplicación exponga hacia fuera). Separar credenciales de aplicación de credenciales de persona es también cómo Stripe protege su API (keys) frente a su Dashboard (login de usuario) — dos mecanismos distintos para dos audiencias distintas es lo esperado, no la excepción.

### 3.1 Seguridad en dos capas (*defense in depth*)

1. **HTTP Basic Auth** delante de `/admin/*` — proporcionado para un único administrador; no es un parche temporal, es la instancia mínima correcta de "credencial de persona" para esta escala.
2. **IP allowlist** en el reverse proxy, además de la capa anterior, no en su lugar — mismo principio que la guía estándar de seguridad para interfaces de administración (ninguna capa sola es suficiente).

### 3.2 Limitaciones aceptadas conscientemente

- El panel y la API pública comparten ciclo de despliegue (si la API escala, el panel escala con ella, sin necesitarlo) — mismo tipo de riesgo ya aceptado con `shared/`, barato hoy, revisable si algún día hace falta separar.
- Las llamadas del panel nunca pasan por `routers/` (van directas a `services/`) — el panel no sirve como prueba de humo de la capa HTTP pública; si hace falta esa cobertura, la dan los tests de `routers/`, no el uso diario del panel.
- HTTP Basic no da para múltiples administradores con distinto nivel de confianza ni para auditoría por persona — techo real si el equipo crece, no un problema hoy.

---

## 4. Compatibilidad futura con app móvil

El modelo de auth ya contempla esto — fila "App futura" en la tabla de clientes de la API, con scopes a decidir cuando exista. No requiere ningún cambio estructural para soportar lectura + suscripción desde una app móvil el día que se construya.

**Salvedad anotada, sin trabajo asociado todavía:** si la app resulta ser nativa (no una PWA), el sistema de notificaciones necesitará un segundo canal — el diseño actual (`pywebpush`, columnas `push_endpoint`/`push_keys`) es específico de Web Push (navegador/PWA), no de APNs (iOS) ni FCM (Android). Si es una PWA, no hace falta ningún cambio.

---

## 5. Testing

Mismo rigor que el resto del proyecto:

- **Frontend público:** Playwright para flujos completos (buscar → ficha → suscribirse), unitarios para las islas interactivas.
- **Panel de gestor:** extensión natural de los tests que ya existen en `api/` — un test de router HTML comprueba el mismo tipo de efecto que ya se testea en los routers JSON.

---

## 6. Deliberadamente diferido — no forma parte de esta ronda

Para que quede explícito y nadie lo interprete como una decisión pendiente de la que alguien se olvidó:

- **Estética / design system** — se decide después de tener el andamiaje funcional, para no rehacer trabajo visual dos veces.
- **Librería de UI para las islas interactivas de Astro** (React, Svelte, Vue, o incluso sin framework) — se decide al construir la primera isla real (el botón de suscripción o el filtro de búsqueda), no antes.
- **Plataforma de hosting/despliegue** — condiciona detalles de implementación (el equivalente de ISR, por ejemplo) pero no bloquea empezar a construir en local.
- **Reverse proxy concreto para el IP allowlist** (nginx, Caddy...) — solo relevante en el momento de desplegar, no en desarrollo local.

---

## 7. Orden de trabajo propuesto

1. **Panel de gestor primero** — es lo que desatasca la cola de revisión de matching, que lleva bloqueada varias sesiones. Empezar sin estética, HTML plano funcional.
2. **Frontend público después**, empezando por `GET /products` (buscador) de punta a punta antes de replicar el patrón al resto de páginas — mismo criterio ya usado al construir la API.

**Hecho (2026-08-27, primera pasada del panel):** esqueleto de `admin/` dentro de `api/` + `GET /admin/matches` end-to-end (listado con filtro `status`, HTML plano sin estética) más el ciclo completo confirmar/rechazar/reabrir vía htmx sobre la misma cola, siguiendo exactamente `estandares-implementacion-frontend.md` sección 2. HTTP Basic (`admin/auth.py`) delante de todo `/admin/*`. 10 tests nuevos (150 en total). Queda pendiente replicar el patrón a `products`/`stores` del panel, y todo el sitio público (Astro).

**Corregido (2026-08-27, revisión de seguridad):** `verify_admin()` usaba `ADMIN_USERNAME`/`ADMIN_PASSWORD` a `""` como valor por defecto sin configurar -- `secrets.compare_digest("", "")` es `True`, así que un despliegue sin esas dos variables quedaba con el panel completo (lectura y escritura) accesible con credenciales vacías (`curl -u ":"`), verificado en vivo incluyendo la escritura real en BBDD. A diferencia de `API_KEYS_JSON` (sin configurar → diccionario vacío, falla cerrado de verdad porque ninguna key coincide con nada), una cadena vacía sí puede coincidir consigo misma. Arreglado con un check explícito de "¿hay algo configurado?" antes de comparar; 2 tests nuevos que fijan ambas variables a `""` y confirman `401` en lectura y escritura (152 en total).

**Cierre de fase (2026-08-27):** completado todo `docs/plan-cierre-panel-gestor.md` -- las tres áreas de `api-endpoints-gestor.md` (matching, productos, tiendas) tienen ya su página HTML equivalente al endpoint JSON:

- **Matching**: filtro de `status` visible en la cola, formulario de rechazo completo (`mark_as` + `reason`, con `reason` ahora persistido de verdad en `store_product.reviewed_reason` -- hueco real encontrado al construirlo, `reason` se aceptaba en el body desde `api-endpoints-v1.md` v1 pero se descartaba en silencio), y vista de `missing-candidates` con enlace "Crear canónico" que preellena el alta.
- **Productos**: listado con buscador simple, alta y edición (formulario compartido `form.html`), con manejo explícito de `409` por nombre duplicado -- vuelve a mostrar el formulario con lo ya escrito, nunca la página de error genérica de FastAPI.
- **Tiendas**: listado con columna de salud (`lastScrapedAt`, `consecutiveFailures`), detalle + edición de `sitemapUrl`/`active` (`sitemapUrl` no se exponía todavía en `GET /stores/{id}`, hueco real cerrado de paso -- ver `estandares-implementacion-frontend.md`).

**`POST /stores/{id}/scrape` queda explícitamente fuera de esta fase** (`docs/plan-cierre-panel-gestor.md` sección 1.6) -- no es un olvido: `dispatcher.query_store()` vive en `store_monitor/`, con dependencias pesadas (`cloudscraper`, `pybreaker`) que `api/` no tiene a propósito. Ninguna de las tres formas de cruzar ese puente (servicio HTTP interno, cola de trabajos, subproceso) es gratis, y no hay todavía necesidad operativa real que la justifique -- se retoma cuando haga falta de verdad, con su propia ronda de diseño.

185 tests en `api/` (99% de cobertura), suite completa corrida contra Postgres real. **Deliberadamente diferido, sin cambios respecto a la sección 6**: estética/design system, librería de UI para las islas de Astro, hosting/despliegue, reverse proxy para el IP allowlist -- y ahora también el sitio público en Astro completo, que no forma parte de esta ronda (era el paso 2 del orden de trabajo, sección 7).

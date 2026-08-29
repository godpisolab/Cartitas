# Propuesta — Observabilidad y control manual del scraping desde el panel

> **Estado: implementado (2026-08-29).** Los cuatro puntos de abajo están construidos y probados tal cual se decidieron aquí -- ver `docs/api/endpoints-gestor.md` sección "Scraping manual desde el panel" y `store_monitor/README.md` sección "Disparo manual desde el panel..." para el estado final, que es la referencia viva (este documento queda como registro de la decisión, no se sigue actualizando). Ampliación posterior no prevista en el diseño original: el disparo de una tienda suelta deja elegir si persistir el resultado (`persist=true|false`, checkbox en el panel) y muestra los productos encontrados aunque no se persistan (`store_monitor/run_results.py`), para que un disparo de solo diagnóstico sirva de algo más que "sigue funcionando o no".

Cuatro peticiones relacionadas, de peso muy distinto. Empiezo por la que no necesita ninguna decisión, y dejo las otras tres para las que sí hace falta elegir algo antes de construir.

**Contrastado contra patrones establecidos del sector** (`patrones-jobs-progreso.md`) — el diseño de abajo no se aparta de cómo ya se resuelve esto en herramientas de referencia (Shopify/`delayed_job`, Celery, Jenkins/GitHub Actions, las guías de UX de Apple/Google/Microsoft); las referencias concretas están integradas en cada sección.

---

## 1. Enlace al producto de la tienda en el panel de revisión — sin decisiones, listo para implementar

**Estado actual, comprobado:** `store_product.store_url` existe en el esquema y ya se usa internamente, pero `MatchItem` (el schema que devuelve `GET /admin/matches`) no lo expone, y `_row.html` no lo pinta.

**Cambio:**
```python
# api/schemas/matches.py
class MatchItem(CamelModel):
    ...
    store_url: str
```
```python
# api/services/matches.py -- incluir StoreProduct.store_url en el SELECT que ya arma MatchItem
```
```html
<!-- admin/templates/matches/_row.html -->
<a href="{{ item.store_url }}" target="_blank" rel="noopener">Ver en tienda ↗</a>
```

Sin riesgo, sin arquitectura nueva — es añadir un campo que ya existe en la fila a un `SELECT` y a una plantilla.

---

## 2. Logging real a fichero — reemplaza los `print()` sueltos

**Por qué hace falta antes que el punto 3:** un botón que "lanza un scrape" sin ningún rastro persistente de lo que hizo es peor que no tenerlo — hoy `dispatcher.py`/scrapers escriben con `print()` a la terminal, que desaparece en cuanto no hay nadie mirando esa consola en ese momento exacto. Para que el punto 4 (barra de progreso) tenga de dónde leer, hace falta que el progreso quede en un sitio consultable después del hecho, no solo durante.

### Propuesta

- Sustituir `StoreLogger` (hoy imprime a consola) por un logger real de Python (`logging`), con un `FileHandler` por ejecución — no un único fichero infinito que crece para siempre.
- **Un fichero de log por ejecución**, no uno por tienda ni uno global: `logs/{run_id}.log`, donde `run_id` identifica esa ejecución concreta (barrido diario de hoy, o el disparo manual de una tienda desde el panel). Formato de línea consistente y parseable: `%(asctime)s [%(store)s] %(message)s`.
- **Una tabla nueva, `scrape_run`**, para que el panel pueda consultar el estado sin tener que parsear el fichero de log byte a byte cada vez que refresca:

**No es una tabla inventada — es el patrón estándar del sector para este tipo exacto de tarea.** Es la misma idea que `delayed_job`, la librería que **Shopify** extrajo de su propio backend para encolar tareas de fondo (envíos masivos, redimensionado de imágenes, **descargas HTTP por lotes** — el mismo tipo de trabajo que este scraper). Celery, el gestor de tareas de fondo más usado en Python, formaliza el mismo concepto con una máquina de estados algo más rica: `PENDING → STARTED → SUCCESS` / `FAILURE` / `RETRY` / `REVOKED`.

```sql
CREATE TABLE scrape_run (
    id              SERIAL PRIMARY KEY,
    job_type        VARCHAR(30) NOT NULL,   -- 'daily_sweep' | 'hot_refresh' | 'sitemap_poll' | 'single_store'
    store_label     VARCHAR(100),           -- NULL salvo en 'single_store'
    status          VARCHAR(20) NOT NULL,   -- 'running' | 'completed' | 'failed'
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    stores_total    INT,                    -- NULL en 'single_store' (no aplica)
    stores_done     INT DEFAULT 0,
    log_file_path   VARCHAR(255) NOT NULL
);
```

`stores_done` se actualiza cada vez que una tienda termina dentro de esa ejecución (barrido completo) — es el dato que alimenta la barra de progreso del punto 4 sin tener que adivinarlo del log.

**Nota para más adelante, no urgente:** `running`/`completed`/`failed` es un subconjunto simplificado de los estados de Celery. Si en algún momento se plantea migrar de verdad a una cola de tareas (Celery/RQ) en vez de este servicio casero, adoptar ya los nombres de Celery (`STARTED`/`SUCCESS`/`FAILURE`) ahorraría una migración de datos más tarde — no es necesario funcionalmente hoy, solo una elección de nombres barata de cambiar ahora y cara de cambiar después.

---

## 3. Botones para lanzar scrapes (por tienda y por tipo de job) — la decisión de arquitectura que habíamos aparcado

### Por qué la retomo ahora, y no la dejo aparcada otra vez

Cuando se planificó el cierre del panel de gestor, `POST /stores/{id}/scrape` se dejó fuera a propósito — el coste de cruzar `api/` (ligero) con `store_monitor/` (dependencias pesadas: `cloudscraper`, `pybreaker`) para una única acción ocasional no compensaba. **Eso ha cambiado**: ahora no es una acción suelta, son **varias** (tres tipos de job + scrape por tienda) que además necesitan estado persistente y seguimiento de progreso — el cálculo de coste/beneficio es distinto con este alcance.

### Propuesta: un pequeño servicio propio dentro de `store_monitor/`, no un import de Python

`scheduler.py` ya es un proceso persistente en `store_monitor/` que sabe invocar los tres tipos de job (`job_daily_sweep`, `job_hot_refresh`, `job_sitemap_poll`) y, vía `dispatcher.query_store()`, el scrape de una tienda suelta. Se le añade una API HTTP mínima (FastAPI, sin nada más) **en el mismo proceso**, que expone:

```
POST /jobs/daily-sweep          -> crea scrape_run, lanza el job en background, devuelve {run_id}
POST /jobs/hot-refresh          -> ídem
POST /jobs/sitemap-poll         -> ídem
POST /jobs/store/{label}        -> ídem, job_type='single_store'
GET  /jobs/runs/{run_id}        -> status, stores_done/stores_total, started_at, finished_at
GET  /jobs/runs/{run_id}/log    -> contenido del fichero de log (o las últimas N líneas)
```

`api/admin/routes/stores.py` llama a esta API por HTTP (no importa nada de `store_monitor/` en Python) — es un servicio de verdad, no el puente que ya se descartó para `classify`/`domain`. La diferencia con aquel caso: aquí sí hace falta un proceso vivo con estado (qué se está ejecutando ahora mismo), no solo funciones puras — es la situación para la que HTTP entre servicios es la herramienta correcta, no una sobre-ingeniería.

**Seguridad:** este servicio interno **no se expone públicamente** — solo escucha en localhost o en la red interna donde vive `api/`, igual que ya se decidió para el propio panel de gestor (capas de red + autenticación, no una sola). No necesita su propio HTTP Basic si la red ya lo aísla; si se despliega en un host distinto al de `api/`, sí hace falta algo de autenticación mínima entre los dos (un token compartido por variable de entorno, nada más elaborado).

### Botones en el panel

- **Vista de una tienda** (`GET /admin/stores/{id}`): botón "Lanzar scrape ahora" → `POST /jobs/store/{label}` en el servicio interno → guarda `run_id` en la página vía htmx.
- **Un sitio nuevo, `GET /admin/jobs`**: tres botones, uno por tipo (`daily-sweep`/`hot-refresh`/`sitemap-poll`), útil para forzar un barrido completo sin esperar al cron — y un historial de las últimas ejecuciones (`scrape_run` ya lo registra).

**Esta forma (botón que dispara + log en vivo + historial de ejecuciones) tampoco hay que diseñarla desde cero — es la que ya usáis a diario.** Es, en esencia, una versión mínima de lo que hace el propio `tests.yaml` de CI en cada `push` (GitHub le asigna un identificador a la ejecución, muestra el log en streaming, guarda el historial consultable después) o de la página de un job de Jenkins (botón "Build Now" + consola en vivo + historial de builds). No es una forma nueva de resolver esto, es la misma que ya conocéis de usar.

---

## 4. Barra de progreso — el barrido completo es fácil, una tienda suelta necesita un paso previo

**Terminología, no inventada — estándar en Apple HIG, Google Material y Microsoft Fluent, las tres guías coinciden:** un indicador de progreso es **determinado** (se conoce el total, barra con porcentaje real) o **indeterminado** (no se conoce, spinner sin porcentaje). Las tres guías avisan de lo mismo: mostrar un progreso que avanza rápido al principio y se estanca al final "puede sentirse engañoso" — el riesgo concreto de una estimación mal calibrada, justo lo que hay que evitar abajo.

### Barrido completo (todas las tiendas) — determinado desde el primer instante, con lo de arriba ya basta

Con `scrape_run.stores_done`/`stores_total` ya mantenidos por el servicio de jobs, el panel solo necesita sondear:

```html
<div hx-get="/admin/jobs/runs/{{ run_id }}" hx-trigger="every 2s" hx-swap="outerHTML">
  <progress value="{{ run.stores_done }}" max="{{ run.stores_total }}"></progress>
  {{ run.stores_done }} / {{ run.stores_total }} tiendas
</div>
```

El total (`stores_total = len(runnable_stores)`) se conoce desde el primer instante — no hace falta ningún descubrimiento previo. Caso determinado limpio, sin matices.

### Scrape de una sola tienda — el total NO se conoce de antemano, y depende de la plataforma

Aquí el "total" es número de páginas o de productos dentro de esa tienda, y **no todas las plataformas lo dan gratis**:

| Plataforma | ¿Se conoce el total por adelantado? |
|---|---|
| **WooCommerce** | **Sí** — la Store API devuelve la cabecera `X-WP-TotalPages` ya en la primera petición, sin coste extra (determinado real) |
| Shopify, PrestaShop, Odoo, OpenCart, genérico JSON-LD | No — se pagina siguiendo enlaces "siguiente" hasta que no hay más, sin ningún conteo total adelantado |

**Propuesta, decidida:** estimación cacheada, no descubrimiento en cada disparo. Guardar el último recuento real conocido por tienda (`store.last_known_page_count`, actualizada al final de cada scrape real, sea manual o del barrido programado) y usarlo como estimación para la barra de progreso: `"Página 4 de ~15 (estimado)"`. Si el número real difiere un poco de una vez a otra (la tienda añadió/quitó productos), la barra se ajusta sola en cuanto termina esa pasada, sin que haya costado ni una petición extra — no se evalúa el camino de "pasada de descubrimiento previa" precisamente por el coste que tiene para la mayoría de plataformas (ver abajo).

**Este intermedio tampoco es una invención nuestra** — ni puramente determinado ni indeterminado, es el patrón reconocido en las mismas guías de diseño para "se conoce el total aproximado por experiencia previa, pero no con certeza esta vez", siempre que se comunique como estimación (de ahí el `~` y la etiqueta explícita) en vez de presentarlo como un dato exacto que luego decepciona.

**Por qué se descarta el descubrimiento previo, con coste real medido:** comprobado contra el código de los scrapers, `next_url`/`has_next` en PrestaShop, Odoo, OpenCart y el fallback HTML de WooCommerce se obtiene parseando el **HTML completo de cada página** (`soup.select_one(...)` sobre `resp.text`) — no hay ningún endpoint ligero de "solo el conteo". Una pasada de descubrimiento tendría que descargar exactamente las mismas páginas que la pasada real (mismas peticiones, mismo tiempo de `crawl-delay`), duplicando tiempo de espera y carga contra el servidor de esa tienda. Con una tienda de 15 páginas y `crawl-delay` de 2s: ~35-40s hoy → ~70-80s con descubrimiento + real. Solo la Store API de WooCommerce da el total gratis (`X-WP-TotalPages`, en la misma petición que ya trae los datos), sin necesitar ninguna pasada aparte — para esa plataforma la barra ya es exacta desde el primer momento, sin necesidad de caché.

---

## Orden de trabajo sugerido

1. **Punto 1** ya — trivial, sin dependencias de nada más.
2. **Punto 2** (logging + tabla `scrape_run`) — es la base de la que dependen 3 y 4, no se puede saltar.
3. **Punto 3** (servicio interno + botones) — usa lo del punto 2.
4. **Punto 4** (barra de progreso) — gratis una vez existen 2 y 3.

¿Confirmamos este enfoque (servicio HTTP interno en `store_monitor/`, tabla `scrape_run`, un fichero de log por ejecución) antes de que lo prepares para el próximo commit, o quieres que ajuste algo del diseño primero?

# Patrones de la industria — jobs, disparo bajo demanda y progreso

Antes de construir `scrape_run` desde cero, comprobación de si esto ya está resuelto en algún sitio. Resumen: sí, tres veces — y el diseño que ya teníamos encaja con el patrón estándar casi sin retocar.

---

## 1. La "tabla de jobs" — no es una tabla que inventamos, es EL patrón

Lo que propusimos como `scrape_run` (una fila por ejecución, con `status`, timestamps, contador de progreso) es exactamente el patrón que usan las herramientas de referencia del sector:

- **`delayed_job`** — extraída directamente de **Shopify** (la propia empresa, no una analogía nuestra) para encolar tareas de fondo: envío masivo de emails, redimensionado de imágenes, **descargas HTTP**, importaciones por lotes. Es, literalmente, el mismo tipo de trabajo que hace vuestro scraper, resuelto con una tabla `delayed_jobs`.
- **Celery** (el gestor de tareas de fondo más usado en Python) define sus estados de tarea como una máquina de estados fija: `PENDING → STARTED → SUCCESS` / `FAILURE` / `RETRY` / `REVOKED`. Nuestro `running/completed/failed` es un subconjunto más simple de exactamente esa misma máquina de estados — no hace falta inventar nombres de estado nuevos, se pueden adoptar los ya estandarizados si algún día conviene más granularidad (por ejemplo, `RETRY` si algún día los scrapes fallidos se reintentan solos).

**Conclusión: `scrape_run` no es una ocurrencia nuestra — es la aplicación directa del patrón "jobs table" que ya usa la propia Shopify para el mismo tipo de tarea.** No hay que rediseñarlo, solo confirmar que los campos que ya propusimos cubren lo mismo que estas referencias.

---

## 2. Barra de progreso determinada vs. indeterminada — terminología estándar, no una elección de estilo

Comprobado contra las guías de diseño de Apple (Human Interface Guidelines), Google (Material Design) y Microsoft (Fluent UI) — las tres coinciden en la misma distinción binaria:

- **Determinado**: se conoce la duración/cantidad total, se muestra una barra con porcentaje real.
- **Indeterminado**: no se conoce, se muestra un indicador de actividad (spinner) sin pretender un porcentaje.

Regla explícita de las tres guías: *"usar determinado siempre que sea posible; un indicador indeterminado no ayuda a estimar cuánto queda"* — y, más importante para nuestro caso, una advertencia explícita de Apple: **mostrar un progreso que avanza rápido al principio y se estanca al final "puede sentirse engañoso"** — justo el riesgo de una estimación mal calibrada.

**Dónde encaja nuestra "estimación cacheada" (`página 4 de ~15`):** no es ni puramente determinado ni puramente indeterminado — es un patrón intermedio reconocido, a veces llamado *progreso optimista/estimado*, que estas mismas guías aceptan siempre que se comunique que es una estimación (de ahí el `~`), no un dato exacto. No es una invención nuestra tampoco — es la solución estándar para "sabemos aproximadamente el total por experiencia previa, pero no con certeza esta vez".

---

## 3. Disparo bajo demanda + logs en vivo — el patrón ya lo conocéis de usarlo

El patrón "botón que lanza una tarea larga + ver su log en directo + historial de ejecuciones anteriores" no hace falta diseñarlo desde cero — es exactamente **lo que ya hace el propio `tests.yaml` que montamos para CI**: cada `push` dispara un "job", GitHub le asigna un identificador, muestra el log en streaming mientras corre, y guarda el historial de ejecuciones anteriores consultable después. Jenkins (el equivalente self-hosted más conocido) es el mismo patrón exacto, más maduro: botón "Build Now", consola en vivo, historial de builds con su estado.

**Conclusión práctica:** el diseño de `GET /admin/jobs` (botones + historial) y `GET /jobs/runs/{run_id}/log` que ya propusimos es, en esencia, una versión mínima de la página de un "run" de GitHub Actions — no hace falta mirar más lejos para saber que la forma es la correcta, ya la usáis a diario sin haberla construido vosotros.

---

## Conclusión general

Nada de lo diseñado hasta ahora (`scrape_run`, servicio HTTP interno para lanzar jobs, `htmx` sondeando el estado, estimación cacheada para el progreso de una tienda suelta) se aparta de cómo el sector ya resuelve esto — al contrario, coincide con el patrón de una empresa del mismo sector (Shopify) para el mismo tipo de tarea (descargas HTTP por lotes). No hace falta rediseñar nada de lo ya propuesto en `propuesta-scraping-manual-panel.md`; esto sirve como confirmación externa, no como corrección.

**Único ajuste que sugeriría, menor:** adoptar los nombres de estado de Celery donde ya coinciden (`running`→`STARTED`, `completed`→`SUCCESS`, `failed`→`FAILURE`) no es necesario funcionalmente, pero si en algún momento se plantea migrar de verdad a Celery/RQ para las tareas de fondo (en vez del servicio HTTP casero propuesto), empezar con esos nombres desde ahora ahorraría una migración de datos más adelante. No es un cambio urgente, solo una nota para cuando se escriba el código.

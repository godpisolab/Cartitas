# Plan de cierre — Panel de gestor

Todo lo que falta para dar por terminada esta fase, en el orden en que tiene sentido construirlo. Cada paso incluye sus tests — no se separan en una fase aparte de "luego testeamos", mismo estándar que el resto del proyecto.

---

## 0. Definición de "terminado" para esta fase

El panel de gestor se da por cerrado cuando:

1. Las tres áreas de `docs/api/endpoints-gestor.md` (matching, productos, tiendas) tienen página HTML equivalente a su endpoint JSON ya construido.
2. Cada acción de escritura tiene su test con credenciales válidas, sin credenciales, y con `ADMIN_USERNAME`/`ADMIN_PASSWORD` sin configurar (patrón fijado en `2857e44`, ver sección 3.1).
3. `docs/frontend/arquitectura-decidida.md` tiene una entrada final marcando la fase como cerrada, con lo que quedó fuera y por qué (igual que ya se hizo con el primer commit del panel).

`POST /stores/{id}/scrape` **no** forma parte de esta definición de "terminado" — se deja fuera a propósito (sección 1.6).

---

## 1. Pasos pendientes, en orden

### 1.1 Filtro de `status` visible en la cola de matching

**Por qué primero:** es el más barato (la API ya soporta los cuatro valores; solo falta la interfaz) y el que más se nota ahora mismo — auditar lo ya confirmado es imposible sin editar la URL a mano.

**Qué construir:** en `list.html`, un desplegable/enlaces que naveguen a `/admin/matches?status=X`, con el valor actual resaltado.

```html
<!-- templates/matches/list.html, dentro de {% block content %} antes de la tabla -->
<nav class="status-filter">
  {% for value, label in [("needsReview", "Pendientes"), ("unmatched", "Sin match"), ("confirmed", "Confirmados"), ("all", "Todos")] %}
    <a href="/admin/matches?status={{ value }}" class="{{ 'active' if status == value }}">{{ label }}</a>
  {% endfor %}
</nav>
```

`admin/routes/matches.py::list_matches` ya recibe `status` como query param y se lo pasa a `matches_service.list_pending` — solo hace falta pasar también `status` al contexto de la plantilla para poder resaltar el activo.

**Tests:**
- `test_filtro_status_confirmed_muestra_solo_confirmados` — sembrar una fila `needsReview` y otra `confirmed`, pedir `?status=confirmed`, comprobar que el HTML devuelto contiene la segunda y no la primera.
- `test_enlaces_de_filtro_presentes_en_cada_valor` — comprobar que las 4 opciones aparecen en el HTML de cualquier respuesta (no solo cuando hay resultados).

### 1.2 Completar la interfaz de rechazo

**Qué falta:** el botón "Descartar" manda siempre `mark_as: "unmatched"` — la API permite `needsReview` también. Y no hay ningún campo para `reason` (texto libre, ya soportado por `RejectBody`).

**Qué construir:** sustituir el botón único por un pequeño formulario inline (sigue siendo htmx, no JavaScript propio):

```html
<!-- templates/matches/_row.html, sustituyendo el <button> de "Descartar" -->
<form hx-post="/admin/matches/{{ item.store_product_id }}/reject"
      hx-target="closest tr" hx-swap="outerHTML">
  <select name="mark_as">
    <option value="unmatched">Sin match</option>
    <option value="needsReview">Seguir revisando</option>
  </select>
  <input type="text" name="reason" placeholder="Motivo (opcional)">
  <button type="submit">Descartar</button>
</form>
```

**Tests:**
- `test_reject_con_needs_review_deja_ese_estado` (hoy solo se testea con `unmatched` implícito).
- `test_reject_guarda_reason_cuando_se_proporciona` — comprobar en BBDD que `reason` no viene vacío tras el POST (si `RejectBody`/`services.matches.reject` no persisten `reason` todavía en ningún sitio, este es el momento de añadir esa columna/uso — revisar si existe antes de asumir que solo falta la interfaz).

### 1.3 Vista de `missing-candidates`

**Qué construir:** página nueva `GET /admin/missing-candidates`, listando lo que ya devuelve `matches_service.missing_candidates()`, con un enlace "Crear canónico" por fila que preellene el formulario de alta de producto (sección 1.4) vía query params (`?productType=ONE_PIECE&mainSet=OP17&language=EN&packaging=display`).

```python
# admin/routes/matches.py
@router.get("/missing-candidates", response_class=HTMLResponse)
def missing_candidates(request: Request, session: Session = Depends(get_session), min_stores: int = 2):
    items = matches_service.missing_candidates(session, min_stores)
    return templates.TemplateResponse("matches/missing_candidates.html", {"request": request, "items": items})
```

**Tests:**
- `test_missing_candidates_lista_agrupaciones_reales` — sembrar 2+ `store_product` sin match del mismo `(product_type, main_set, language)`, comprobar que aparece agrupado con el `storeCount` correcto.
- `test_missing_candidates_respeta_min_stores` — con `min_stores=3` y solo 2 tiendas coincidentes, no debe aparecer.
- `test_enlace_crear_canonico_preellena_el_formulario` — comprobar que el HTML del enlace lleva los query params correctos.

### 1.4 Panel de productos — listado, alta, edición

**El más grande de los que faltan.** Tres páginas:

- `GET /admin/products` — listado con buscador simple (reutiliza `products_service.search`).
- `GET /admin/products/new` (formulario) + `POST /admin/products` (acción) — alta de canónico. Prellenar desde query params si viene de `missing-candidates` (1.3).
- `GET /admin/products/{id}/edit` (formulario) + `POST /admin/products/{id}` (acción, llama a `PATCH` internamente vía `services.products.update`) — edición, incluido marcar/desmarcar `isHot` + `hotUntil`.

```
api/admin/
├── routes/
│   └── products.py        -- nuevo
└── templates/
    └── products/
        ├── list.html
        ├── form.html       -- compartido entre alta y edición (mismo formulario, distinto action)
        └── _row.html        -- opcional, solo si el listado también se actualiza vía htmx
```

**Tests:**
- `test_alta_producto_desde_panel_devuelve_redirect_a_ficha` (o al listado — decidir el flujo de UX: tras crear, ¿a dónde va?).
- `test_alta_producto_duplicado_muestra_error_en_formulario` — mismo `nameCanonical`+`gameId` ya existente → el formulario vuelve a mostrarse con el mensaje de error, no una página de error genérica de FastAPI.
- `test_editar_marca_is_hot_correctamente` — comprobar en BBDD que `is_hot`/`hot_until` cambian tras el POST.
- `test_formulario_alta_preellenado_desde_query_params` — visitar `/admin/products/new?productType=ONE_PIECE&mainSet=OP17&packaging=display` y comprobar que esos valores aparecen ya puestos en el HTML del formulario.

### 1.5 Panel de tiendas — listado, detalle, edición

- `GET /admin/stores` — listado simple, con columna de salud (`lastScrapedAt`, `consecutiveFailures`) para triage rápido.
- `GET /admin/stores/{id}` — detalle (ya casi calcado del `StoreDetail` que expone `routers/stores.py`).
- Formulario de edición inline o página aparte para `PATCH` — `sitemapUrl` y `active`.

**Tests:**
- `test_listado_tiendas_muestra_columna_salud`.
- `test_editar_sitemap_url_se_refleja_en_bbdd`.
- `test_desactivar_tienda_pone_active_false` — y, si hace sentido en esta fase, un test de integración que confirme que `dispatcher.run_all_stores()` (en `store_monitor/`, no en `api/`) respeta ese cambio — ya cubierto por la suite de `store_monitor/`, no haría falta repetirlo aquí, solo verificar que el panel escribe el valor correcto.

### 1.6 `POST /stores/{id}/scrape` — deliberadamente fuera de esta fase

No se construye en esta ronda. Motivo: `dispatcher.query_store()` vive en `store_monitor/`, que tiene dependencias pesadas (`cloudscraper`, `pybreaker`) — no es candidato a moverse a `shared/` (esas dependencias son precisamente la razón de que `shared/` exista, para no tener que arrastrarlas a `api/`). Llamarlo desde `api/` necesita resolver cómo cruzan los dos servicios sin repetir el acoplamiento que se resolvió para `classify`/`domain`, y eso no tiene una respuesta barata:

| Opción | Coste |
|---|---|
| `store_monitor/` expone un pequeño servicio HTTP interno propio, `api/` lo llama por red | Requiere un proceso persistente nuevo en `store_monitor/`, hoy solo corre como batch programado |
| Cola de trabajos (tabla o Redis) que un worker de `store_monitor/` consume | Infraestructura nueva para una acción de uso muy poco frecuente |
| `api/` ejecuta `store_monitor/` como subproceso | Requiere las dependencias de `store_monitor/` instaladas en la misma máquina que `api/`, rompe el aislamiento que se buscaba |

Ninguna es gratis, y no hay todavía una necesidad operativa real que la justifique (el barrido diario ya cubre el caso general). Se deja anotado aquí para no perderlo, igual que Alembic o `Idempotency-Key` — se retoma cuando haga falta de verdad, no antes.

> **Retomado y resuelto (2026-08-29):** se eligió la primera opción de la tabla (servicio HTTP interno) — ver `docs/propuestas/propuesta-scraping-manual-panel.md` y `docs/api/endpoints-gestor.md` sección "Scraping manual desde el panel" para el diseño final. La nota de abajo sigue siendo correcta como registro de por qué se dejó fuera de ESTA fase.

---

## 2. Guías / lecciones aprendidas a aplicar en todo lo de arriba

### 2.1 El patrón del bug de auth (`2857e44`) generaliza — aplicarlo como checklist, no solo como anécdota

Cualquier valor de configuración que se compare directamente (no que se busque como clave en un diccionario) es sospechoso si su default-sin-configurar es una cadena vacía. Antes de dar cualquier pieza nueva de `admin/` por terminada, repasar: **¿hay algún `os.environ.get(X, "")` seguido de una comparación directa (`==`, `compare_digest`, `in` sobre un string) en vez de una búsqueda en colección?** Si sí, mismo arreglo: rechazar explícitamente el caso "no configurado" antes de comparar.

### 2.2 Reutilizar `services/`, nunca lógica nueva en `admin/routes/`

Ya establecido, se repite aquí porque es la regla que más fácil se rompe al ir con prisa: si una plantilla nueva necesita un dato que `services/` no expone todavía, se añade a `services/` (y se reutiliza también desde `routers/` si tiene sentido), no se calcula inline dentro de la función de `admin/routes/`.

### 2.3 Todo formulario de escritura necesita su versión "credenciales vacías → 401"

Extensión directa de 2.1: cada test nuevo de una acción de escritura del panel debe incluir, además del caso feliz y el de credenciales incorrectas, el caso `ADMIN_USERNAME`/`ADMIN_PASSWORD` sin configurar — mismo patrón que los dos tests añadidos en `2857e44`. No es opcional, es la clase de bug que ya se coló una vez.

### 2.4 Autoescape de Jinja2 — no tocarlo sin motivo

Ya verificado que `Jinja2Templates` de FastAPI lo activa por defecto para `.html`, protegiendo contra XSS almacenado vía `raw_name`/`raw_variant` (contenido no confiable, viene del scraper). Si en algún momento se necesita imprimir HTML sin escapar (por ejemplo, para un resumen con formato), usar `{% autoescape false %}` de forma **local y comentada**, nunca desactivarlo globalmente en `templates_env.py`.

### 2.5 Formularios que fallan deben volver a mostrarse con el error, no reventar

(Relevante para 1.4, alta/edición de productos.) Un `409` de nombre duplicado o un `422` de validación no debería traducirse en la página de error por defecto de FastAPI — la ruta de `admin/routes/products.py` debe capturar la excepción de dominio (`errors.py`, ya centralizado para la API JSON) y volver a renderizar `form.html` con los datos que la persona ya había escrito, más el mensaje de error. Perder lo ya escrito en un formulario de administración es el tipo de fricción que hace que la herramienta se evite en vez de usarse.

---

## 3. Checklist de cierre final

- [x] 1.1 a 1.5 implementados, cada uno con sus tests de la sección correspondiente.
- [x] Cada acción de escritura nueva tiene su test de "admin sin configurar → 401" (2.3).
- [x] `docs/frontend/arquitectura-decidida.md` actualizado con una entrada final de cierre de fase, listando explícitamente que `POST /stores/{id}/scrape` queda fuera y por qué (1.6) — para que no se lea como un olvido dentro de unos meses.
- [x] `api/README.md` actualizado con el recuento final de rutas de `admin/` y el número de tests, mismo formato que ya se usa ahí.
- [x] Suite completa de `api/` corrida contra Postgres real antes de dar la fase por cerrada — no basta con que cada test pase aislado.

**Hecho (2026-08-27):** fase cerrada. Commit `6b34608` ("Completar el panel de gestor -- productos, tiendas y cola de matching"). 189 tests en `api/`, 99% cobertura. De paso, revisando la cola de matching real con datos de producción, se encontraron y corrigieron varias mejoras al propio motor de matching (`shared/classify.py`, `store_monitor/matcher.py`) que no formaban parte de este plan pero surgieron directamente de usar el panel recién construido -- documentadas por separado en el commit `84cd703`, no en este plan.

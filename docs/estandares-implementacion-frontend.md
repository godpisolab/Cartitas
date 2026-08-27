# Guía de implementación — Frontend

Aplica los mismos principios de `estandares_organizacion_codigo.md` y `estandares-implementacion-api.md` a las dos piezas de frontend decididas en `frontend-arquitectura-decidida.md`: sitio público (Astro) y panel de gestor (dentro de `api/`). No repite las decisiones de arquitectura ya tomadas — esto es el **cómo se organiza el código** que las implementa.

---

## Parte 1 — Sitio público (Astro)

### 1.1 Dónde vive

```
Cartitas/
├── shared/
├── store_monitor/
├── api/
└── web/              -- nuevo, paquete hermano de los otros tres
```

### 1.2 Estructura interna

```
web/
├── astro.config.mjs
├── package.json
├── .env                         -- CARTITAS_API_KEY (server-only, ver 1.4)
├── src/
│   ├── pages/                   -- rutas (file-based routing de Astro)
│   │   ├── index.astro
│   │   ├── productos/
│   │   │   ├── index.astro      -- buscador
│   │   │   └── [id].astro       -- ficha de producto
│   │   ├── restocks.astro       -- feed de restocks recientes
│   │   └── api/                 -- rutas de SERVIDOR (BFF), no páginas -- ver 1.4
│   │       ├── subscribe.ts
│   │       └── unsubscribe.ts
│   ├── components/              -- .astro puros, CERO JavaScript de cliente
│   ├── islands/                 -- interactivos, hidratados -- ver 1.3
│   ├── layouts/
│   │   └── Layout.astro
│   └── lib/
│       └── cartitas-api.ts      -- único punto que llama a la API real -- ver 1.5
└── tests/
    ├── e2e/                     -- Playwright
    └── unit/                    -- islas aisladas
```

### 1.3 `components/` vs `islands/` — misma disciplina que `routers/` vs `services/` en la API

**Regla: si un componente necesita `client:load`/`client:visible` (las directivas de hidratación de Astro) o cualquier estado de React/Svelte, vive en `islands/`. Si no, vive en `components/`.** La tabla de precios de una ficha de producto, el layout, las tarjetas de resultado de búsqueda — todo eso es `components/`, sin excepción, aunque parezca "casi interactivo". El botón "avísame", el filtro de búsqueda, el gráfico de histórico — eso es `islands/`.

Esta separación física (no solo una convención de nombres) hace verificable de un vistazo cuánto JavaScript envía cada página — si `islands/` empieza a crecer más de lo esperado, es una señal visible de que se está perdiendo el motivo por el que se eligió Astro.

**Decisión de librería de UI para `islands/`** — deliberadamente diferida (`frontend-arquitectura-decidida.md` sección 6). Al construir la primera isla real, evaluar si hace falta React/Svelte de verdad o si Alpine.js (sin build step, pensado para exactamente este tamaño de interactividad) basta — no asumir React por defecto solo porque es lo más común.

### 1.4 El patrón BFF, en código

Las rutas de servidor de Astro (`src/pages/api/*.ts`, no confundir con las páginas de `src/pages/*.astro`) son las únicas que conocen la API key real:

```typescript
// src/pages/api/subscribe.ts
export async function POST({ request }) {
  const body = await request.json();
  const res = await fetch(`${import.meta.env.CARTITAS_API_URL}/subscriptions`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${import.meta.env.CARTITAS_API_KEY}`, // sin prefijo PUBLIC_
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  return new Response(await res.text(), { status: res.status });
}
```

**Regla no negociable de nombres de variable de entorno:** Astro expone al navegador cualquier variable con el prefijo `PUBLIC_` (`import.meta.env.PUBLIC_*`). `CARTITAS_API_KEY` **nunca** lleva ese prefijo. La única variable que sí lo lleva a propósito es la clave pública VAPID (`PUBLIC_VAPID_KEY`), porque esa sí está pensada para ser pública (1.6).

Un componente/isla del lado del cliente que necesite suscribir a alguien llama a `/api/subscribe` (ruta propia, mismo origen) — nunca a la URL real de la API de Cartitas directamente.

### 1.5 `lib/cartitas-api.ts` — único punto de llamada a la API real para lecturas

Mismo principio que `services/` en `api/`: centralizar, no repetir `fetch()` sueltos por cada página.

```typescript
// src/lib/cartitas-api.ts
const BASE = import.meta.env.CARTITAS_API_URL;
const KEY = import.meta.env.CARTITAS_API_KEY;

async function get(path: string) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Authorization: `Bearer ${KEY}` },
  });
  if (!res.ok) throw new ApiError(res.status, await res.json());
  return res.json();
}

export const searchProducts = (params: URLSearchParams) => get(`/products?${params}`);
export const getProduct = (id: number) => get(`/products/${id}`);
export const getRestockEvents = (params?: URLSearchParams) => get(`/restock-events?${params ?? ""}`);
```

Como esto se ejecuta en el servidor durante SSR/SSG (nunca en el navegador), usar la key real aquí es seguro — es exactamente lo que hace que la key deje de estar expuesta (`frontend-arquitectura-decidida.md` sección 2.1).

Las páginas (`src/pages/productos/[id].astro`) importan de aquí, nunca hacen `fetch()` directo a la API:

```astro
---
import { getProduct } from "../../lib/cartitas-api";
const product = await getProduct(Astro.params.id);
---
```

### 1.6 Web Push — lo mínimo que hace falta saber antes de construirlo

- `PUBLIC_VAPID_KEY` en `.env` — esta sí lleva el prefijo, es pública por diseño del protocolo.
- El registro del Service Worker (`public/sw.js` + `navigator.serviceWorker.register()`) vive en la isla del botón "avísame", no en `Layout.astro` — no tiene sentido registrar el Service Worker en páginas donde no hay nada que suscribir.

### 1.7 Caché/revalidación

Cada página declara su propia estrategia según la tabla de `frontend-arquitectura-decidida.md` sección 2.2 — en Astro, esto se expresa por página (`export const prerender = true/false`, más configuración del adaptador de hosting para la revalidación periódica). No hay un ajuste global único; se decide fichero por fichero según qué contenido sirve.

### 1.8 Testing

- **Playwright** (`tests/e2e/`): un test por flujo completo de usuario, no por página aislada — `buscar-producto.spec.ts` cubre buscador → filtro → ficha → suscripción, de punta a punta.
- **Unitarios** (`tests/unit/`): solo para la lógica dentro de una isla (por ejemplo, el cálculo de qué mostrar en el gráfico de histórico), no para componentes `.astro` puros — esos se cubren con el propio Playwright al visitarlos.

### 1.9 Checklist antes de añadir una página nueva

- [ ] ¿La página llama a `lib/cartitas-api.ts`, no a `fetch()` suelto?
- [ ] ¿Todo lo que no necesita interactividad está en `components/`, no en `islands/`?
- [ ] ¿Alguna escritura del lado del cliente pasa por una ruta de `pages/api/*.ts` propia, nunca por la API real directamente?
- [ ] ¿La estrategia de caché de esta página está declarada explícitamente, no dejada al default?

---

## Parte 2 — Panel de gestor (dentro de `api/`)

### 2.1 Estructura interna

```
api/
├── routers/              -- JSON, sin cambios
├── services/             -- sin cambios, REUTILIZADO por admin/ (ver 2.3)
├── admin/                -- nuevo
│   ├── __init__.py
│   ├── auth.py           -- HTTP Basic -- mecanismo DISTINTO del Bearer de auth.py, ver 2.2
│   ├── routes/
│   │   ├── matches.py
│   │   ├── products.py
│   │   └── stores.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── matches/
│   │   │   ├── list.html         -- página completa
│   │   │   └── _row.html         -- partial: lo que htmx intercambia tras una acción
│   │   ├── products/
│   │   └── stores/
│   └── static/                   -- CSS mínimo, sin build step ni bundler
└── main.py                       -- monta admin.routes junto a los routers JSON
```

### 2.2 `admin/auth.py` — HTTP Basic, deliberadamente separado de `auth.py`

**No reutilizar `require_scope()`** (Bearer + scopes, pensado para aplicaciones cliente) para el panel — son dos mecanismos de auth distintos a propósito (`frontend-arquitectura-decidida.md` sección 3, el paralelismo con Stripe: API keys para aplicaciones, login para personas). Mezclarlos en el mismo fichero invitaría a confundir cuál usar donde.

```python
# admin/auth.py
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets

security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    correct_user = secrets.compare_digest(credentials.username, settings.ADMIN_USERNAME)
    correct_pass = secrets.compare_digest(credentials.password, settings.ADMIN_PASSWORD)
    if not (correct_user and correct_pass):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
```

`secrets.compare_digest` en vez de `==` — comparación en tiempo constante, evita timing attacks triviales sobre la contraseña. Aplicado una vez, a nivel de router:

```python
# main.py
app.include_router(admin_matches_router, prefix="/admin", dependencies=[Depends(verify_admin)])
```

### 2.3 Las rutas de `admin/` llaman a `services/` directamente — nunca por HTTP

```python
# admin/routes/matches.py
@router.get("/matches", response_class=HTMLResponse)
def list_matches(request: Request, session: Session = Depends(get_session)):
    items = matches_service.list_pending(session, status="needsReview")  # misma función que usa routers/matches.py
    return templates.TemplateResponse("matches/list.html", {"request": request, "items": items})

@router.post("/matches/{id}/confirm", response_class=HTMLResponse)
def confirm_match(id: int, product_id: int = Form(...), session: Session = Depends(get_session)):
    updated = matches_service.confirm(session, id, product_id)  # misma función, mismo camino de validación
    return templates.TemplateResponse("matches/_row.html", {"request": request, "item": updated})
```

Es la misma relación entre `routers/` y `services/` ya establecida en `estandares-implementacion-api.md` sección 2 — `admin/routes/` es, en la práctica, un segundo "router" que devuelve HTML en vez de JSON, sobre exactamente la misma capa de lógica.

### 2.4 Patrón htmx: la respuesta es un fragmento, no una página completa

```html
<!-- templates/matches/_row.html -->
<tr id="match-{{ item.storeProductId }}">
  <td>{{ item.rawName }}</td>
  <td>
    <button hx-post="/admin/matches/{{ item.storeProductId }}/confirm"
            hx-target="closest tr" hx-swap="outerHTML"
            hx-vals='{"product_id": {{ item.candidates[0].productId }}}'>
      Confirmar
    </button>
  </td>
</tr>
```

Al confirmar, htmx reemplaza solo esa fila (`hx-target="closest tr"`) con lo que devuelva `_row.html` — la página no se recarga entera, y no hace falta ni una línea de JavaScript propio para conseguirlo.

### 2.5 IP allowlist — fuera del código de `api/`

Vive en la configuración del reverse proxy (nginx/Caddy) delante de `/admin/*`, no en FastAPI — es una decisión de despliegue, deliberadamente diferida (`frontend-arquitectura-decidida.md` sección 6). No añadir un middleware de IP a mano en `api/` mientras no exista ese reverse proxy: comprobar `request.client.host` sin más da falsos positivos/negativos en cuanto haya cualquier proxy intermedio (no ve la IP real sin `X-Forwarded-For` bien configurado) — mejor no fingir una protección que no es fiable hasta que el reverse proxy real esté decidido.

### 2.6 Testing

Extensión directa de lo que ya existe en `api/tests/` — mismo `TestClient`, con las credenciales de Basic Auth en la petición:

```python
def test_confirm_match_actualiza_la_fila(client, db_conn):
    response = client.post(
        "/admin/matches/4821/confirm",
        data={"product_id": 301},
        auth=("admin", "test-password"),
    )
    assert response.status_code == 200
    assert "confirmed" in response.text
```

### 2.7 Checklist antes de añadir una página/acción nueva al panel

- [ ] ¿La ruta llama a una función de `services/` ya existente (o nueva, pero en `services/`), nunca contiene lógica de negocio ni SQL inline?
- [ ] ¿Está bajo el router con `dependencies=[Depends(verify_admin)]`, no verificando la auth a mano dentro de la función?
- [ ] Si la acción modifica algo, ¿la plantilla que devuelve es un partial (`_algo.html`) pensado para un `hx-swap`, no una página completa?
- [ ] ¿Tiene un test con `TestClient` + credenciales de Basic Auth, mismo patrón que 2.6?

---

## Siguiente paso natural

Con esta guía, el primer trabajo concreto es el esqueleto del panel (sección 2: `admin/auth.py` + una única ruta end-to-end, `GET /admin/matches` con su `list.html`) antes de replicar el patrón a `products`/`stores` — mismo criterio de "un caso completo antes de generalizar" ya usado al construir la API pública.

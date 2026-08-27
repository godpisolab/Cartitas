"""Entry point de la API (`uvicorn main:app`). Único módulo que junta
routers + exception handlers + CORS -- ver
docs/estandares-implementacion-api.md, sección 2."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from admin.auth import verify_admin
from admin.routes import matches as admin_matches
from errors import install_exception_handlers
from routers import catalog, deals, matches, products, restock_events, stores, subscriptions

app = FastAPI(title="Cartitas API", version="1.0.0")

install_exception_handlers(app)

# Lista blanca explícita por cliente (docs/estandares-api-app-tcg.md,
# sección 2) -- nunca "*". Vacía por defecto hasta que el frontend/panel
# tengan un dominio real; se configura en despliegue, no aquí a mano.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(products.router)
app.include_router(deals.router)
app.include_router(restock_events.router)
app.include_router(stores.router)
app.include_router(catalog.router)
app.include_router(subscriptions.router)
app.include_router(matches.router)

# Panel de gestor -- HTML server-rendered en el mismo proceso que la API
# JSON, HTTP Basic propio en vez de Bearer+scope (docs/
# frontend-arquitectura-decidida.md sección 3). `verify_admin` se aplica
# una única vez aquí, a nivel de router -- las rutas de admin/routes/ no lo
# comprueban a mano.
app.include_router(
    admin_matches.router, prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin)],
)
app.mount(
    "/admin/static", StaticFiles(directory=Path(__file__).parent / "admin" / "static"), name="admin-static",
)

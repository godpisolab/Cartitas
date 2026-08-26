"""Entry point de la API (`uvicorn main:app`). Único módulo que junta
routers + exception handlers + CORS -- ver
docs/estandares-implementacion-api.md, sección 2."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

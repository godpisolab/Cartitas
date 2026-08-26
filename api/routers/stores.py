"""Router de tiendas: lectura pública/panel + PATCH de administración."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

import services.stores as stores_service
from auth import require_scope
from db import get_session
from schemas.stores import StoreDetail, StorePatch, StoreSummary

router = APIRouter()


@router.get("/stores")
def list_stores(
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read")),
) -> dict[str, list[StoreSummary]]:
    return {"data": stores_service.list_stores(session)}


@router.get("/stores/{store_id}", response_model=StoreDetail)
def get_store(
    store_id: int,
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read")),
) -> StoreDetail:
    return stores_service.get_store(session, store_id)


@router.patch("/stores/{store_id}", response_model=StoreDetail)
def patch_store(
    store_id: int,
    patch: StorePatch,
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("admin:*")),
) -> StoreDetail:
    return stores_service.patch_store(session, store_id, patch)

"""Router de `GET /games` y `GET /categories`."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

import services.catalog as catalog_service
from auth import require_scope
from db import get_session
from schemas.catalog import CategoryNode, GameItem

router = APIRouter()


@router.get("/games")
def list_games(
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read")),
) -> dict[str, list[GameItem]]:
    return {"data": catalog_service.list_games(session)}


@router.get("/categories")
def list_categories(
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read")),
) -> dict[str, list[CategoryNode]]:
    return {"data": catalog_service.list_categories(session)}

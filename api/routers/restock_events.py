"""Router de `GET /restock-events`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

import services.restock_events as restock_events_service
from auth import require_scope
from db import get_session
from schemas.restock_events import RestockEventFilters, RestockEventItem

router = APIRouter()


@router.get("/restock-events")
def list_restock_events(
    game: str | None = Query(default=None),
    category: str | None = Query(default=None),
    hours: int = Query(24, ge=1),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read")),
) -> dict[str, list[RestockEventItem]]:
    filters = RestockEventFilters(game=game, category=category, hours=hours, limit=limit)
    return {"data": restock_events_service.list_recent(session, filters)}

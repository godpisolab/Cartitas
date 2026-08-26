"""Router de `GET /deals`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

import services.deals as deals_service
from auth import require_scope
from db import get_session
from schemas.deals import DealFilters, DealItem

router = APIRouter()


@router.get("/deals")
def list_deals(
    game: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read")),
) -> dict[str, list[DealItem]]:
    filters = DealFilters(game=game, category=category, limit=limit)
    return {"data": deals_service.search_deals(session, filters)}

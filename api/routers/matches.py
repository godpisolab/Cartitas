"""Router del panel de matching -- todo detrás de scope de administración
salvo la propia lectura de la cola (compartida con "read" del panel de
revisión, ver docs/api-endpoints-v1.md sección 0)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

import services.matches as matches_service
from auth import require_scope
from db import get_session
from schemas.common import Page
from schemas.matches import (
    ConfirmBody,
    MatchFilters,
    MatchItem,
    MatchStatusFilter,
    MissingCandidateItem,
    RejectBody,
)

router = APIRouter()


@router.get("/matches/missing-candidates")
def get_missing_candidates(
    minStores: int = Query(2, ge=1),
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("admin:*")),
) -> dict[str, list[MissingCandidateItem]]:
    return {"data": matches_service.missing_candidates(session, minStores)}


@router.get("/matches", response_model=Page[MatchItem])
def list_matches(
    status: MatchStatusFilter = Query("needsReview"),
    storeId: int | None = Query(default=None),
    minSimilarity: float | None = Query(default=None),
    maxSimilarity: float | None = Query(default=None),
    includeReviewed: bool = Query(default=False),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("admin:*")),
) -> Page[MatchItem]:
    filters = MatchFilters(
        status=status, store_id=storeId, min_similarity=minSimilarity, max_similarity=maxSimilarity,
        include_reviewed=includeReviewed, page=page, limit=limit,
    )
    return matches_service.list_matches(session, filters)


@router.post("/matches/{store_product_id}/confirm", response_model=MatchItem)
def confirm_match(
    store_product_id: int,
    body: ConfirmBody,
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("admin:*")),
) -> MatchItem:
    return matches_service.confirm_match(session, store_product_id, body)


@router.post("/matches/{store_product_id}/reject", response_model=MatchItem)
def reject_match(
    store_product_id: int,
    body: RejectBody,
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("admin:*")),
) -> MatchItem:
    return matches_service.reject_match(session, store_product_id, body)


@router.post("/matches/{store_product_id}/reopen", response_model=MatchItem)
def reopen_match(
    store_product_id: int,
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("admin:*")),
) -> MatchItem:
    return matches_service.reopen_match(session, store_product_id)

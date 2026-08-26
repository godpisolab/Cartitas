"""Router de suscripciones de restock."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlmodel import Session

import services.subscriptions as subscriptions_service
from auth import require_scope
from db import get_session
from schemas.subscriptions import SubscriptionCreate, SubscriptionListItem, SubscriptionSummary

router = APIRouter()


@router.post("/subscriptions", response_model=SubscriptionSummary, status_code=201)
def create_subscription(
    body: SubscriptionCreate,
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("write:subscriptions")),
) -> SubscriptionSummary:
    return subscriptions_service.create_subscription(session, body)


@router.delete("/subscriptions/{subscription_id}", status_code=204)
def delete_subscription(
    subscription_id: int,
    pushEndpoint: str = Query(...),
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("write:subscriptions")),
) -> Response:
    subscriptions_service.delete_subscription(session, subscription_id, pushEndpoint)
    return Response(status_code=204)


@router.get("/subscriptions")
def list_subscriptions(
    pushEndpoint: str = Query(...),
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read")),
) -> dict[str, list[SubscriptionListItem]]:
    return {"data": subscriptions_service.list_by_push_endpoint(session, pushEndpoint)}

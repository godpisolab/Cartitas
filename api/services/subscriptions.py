"""Lógica de suscripciones de restock -- docs/api-endpoints-v1.md sección 5."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from models.product import Product
from models.restock_subscription import RestockSubscription
from schemas.subscriptions import SubscriptionCreate, SubscriptionListItem, SubscriptionSummary


def create_subscription(session: Session, data: SubscriptionCreate) -> SubscriptionSummary:
    if session.get(Product, data.product_id) is None:
        raise UnprocessableEntityError(f"No existe el producto {data.product_id}")

    subscription = RestockSubscription(
        product_id=data.product_id, store_id=data.store_id,
        push_endpoint=data.push_endpoint, push_keys=data.push_keys.model_dump(),
    )
    session.add(subscription)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError("Ya existe una suscripción idéntica para este dispositivo") from None

    session.refresh(subscription)
    return SubscriptionSummary(id=subscription.id, product_id=subscription.product_id,
                                store_id=subscription.store_id)


def delete_subscription(session: Session, subscription_id: int, push_endpoint: str) -> None:
    """El pushEndpoint (ya es un secreto largo generado por el navegador)
    actúa como prueba de propiedad -- ver docs/api-endpoints-v1.md, sección
    5, "resuelto (no queda como alerta)": evita migrar restockSubscription.id
    a UUID solo para que no sea adivinable."""
    subscription = session.get(RestockSubscription, subscription_id)
    if subscription is None:
        raise NotFoundError(f"No existe la suscripción {subscription_id}")
    if subscription.push_endpoint != push_endpoint:
        raise ForbiddenError("pushEndpoint no coincide con el de la suscripción")

    session.delete(subscription)
    session.commit()


def list_by_push_endpoint(session: Session, push_endpoint: str) -> list[SubscriptionListItem]:
    subscriptions = session.exec(
        select(RestockSubscription).where(RestockSubscription.push_endpoint == push_endpoint)
    ).all()
    return [
        SubscriptionListItem(id=s.id, product_id=s.product_id, store_id=s.store_id, created_at=s.created_at)
        for s in subscriptions
    ]

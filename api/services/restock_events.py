"""Lógica de `GET /restock-events` -- feed público de las últimas altas de
stock (nivel 1 del diseño funcional), no confundir con la cola de matching
del gestor. Todo `restock_event` tiene `product_id` NOT NULL en el esquema
(B.2: un restock sin match confirmado nunca genera fila aquí), así que no
hace falta filtrar nada adicional por eso."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from models.category import Category
from models.game import Game
from models.product import Product
from models.restock_event import RestockEvent
from models.store import Store
from models.store_product import StoreProduct
from schemas.restock_events import RestockEventFilters, RestockEventItem


def list_recent(session: Session, filters: RestockEventFilters) -> list[RestockEventItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=filters.hours)

    query = (
        select(RestockEvent, Product.name_canonical, Store.name, StoreProduct.current_price)
        .join(Product, Product.id == RestockEvent.product_id)
        .join(StoreProduct, StoreProduct.id == RestockEvent.store_product_id)
        .join(Store, Store.id == StoreProduct.store_id)
        .join(Game, Game.id == Product.game_id)
        .join(Category, Category.id == Product.category_id)
        .where(RestockEvent.detected_at >= cutoff)
        .order_by(RestockEvent.detected_at.desc())
        .limit(filters.limit)
    )
    if filters.game:
        query = query.where(Game.slug == filters.game)
    if filters.category:
        query = query.where(Category.slug == filters.category)

    rows = session.exec(query).all()
    return [
        RestockEventItem(
            product_id=event.product_id, name_canonical=name_canonical, store_name=store_name,
            price=float(price) if price is not None else None, detected_at=event.detected_at,
        )
        for event, name_canonical, store_name, price in rows
    ]

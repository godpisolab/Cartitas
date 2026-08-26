"""Lógica de `GET /deals` -- ranking de mejores ofertas activas
(docs/api-endpoints-v1.md sección 2). Compara el `minPrice` de hoy (mismo
criterio que services/products.py: solo `store_product` confirmados)
contra el mínimo de hace exactamente `COMPARISON_WINDOW_DAYS` en
`price_history`. Un producto sin ese día exacto en su histórico
simplemente no entra -- no se compara contra "nunca" ni se infla
artificialmente (alerta de diseño ya resuelta en el propio documento)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from models.category import Category
from models.game import Game
from models.price_history import PriceHistory
from models.product import Product
from models.store import Store
from models.store_product import MatchStatus, StockStatus, StoreProduct
from schemas.deals import COMPARISON_WINDOW_DAYS, DealFilters, DealItem


def _cheapest_store_name(session: Session, product_id: int, price: float) -> str | None:
    return session.exec(
        select(Store.name)
        .join(StoreProduct, StoreProduct.store_id == Store.id)
        .where(
            StoreProduct.product_id == product_id,
            StoreProduct.match_status == MatchStatus.CONFIRMED,
            StoreProduct.current_price == price,
        )
        .limit(1)
    ).first()


def search_deals(session: Session, filters: DealFilters) -> list[DealItem]:
    confirmed = StoreProduct.match_status == MatchStatus.CONFIRMED
    compared_to = date.today() - timedelta(days=COMPARISON_WINDOW_DAYS)

    current_agg = (
        select(
            StoreProduct.product_id.label("product_id"),
            func.min(StoreProduct.current_price).label("min_price"),
            func.bool_or(StoreProduct.stock_status == StockStatus.DISPONIBLE).label("any_in_stock"),
        )
        .where(confirmed)
        .group_by(StoreProduct.product_id)
        .subquery()
    )
    previous_agg = (
        select(
            StoreProduct.product_id.label("product_id"),
            func.min(PriceHistory.price).label("min_price"),
        )
        .join(PriceHistory, PriceHistory.store_product_id == StoreProduct.id)
        .where(confirmed, PriceHistory.scraped_date == compared_to)
        .group_by(StoreProduct.product_id)
        .subquery()
    )

    query = (
        select(Product, current_agg.c.min_price, previous_agg.c.min_price)
        .join(current_agg, current_agg.c.product_id == Product.id)
        .join(previous_agg, previous_agg.c.product_id == Product.id)
        .join(Game, Game.id == Product.game_id)
        .join(Category, Category.id == Product.category_id)
        .where(current_agg.c.any_in_stock.is_(True))
        .where(current_agg.c.min_price < previous_agg.c.min_price)
    )
    if filters.game:
        query = query.where(Game.slug == filters.game)
    if filters.category:
        query = query.where(Category.slug == filters.category)

    rows = session.exec(query).all()

    deals = []
    for product, current_price, previous_price in rows:
        current_price, previous_price = float(current_price), float(previous_price)
        drop_percentage = round((previous_price - current_price) / previous_price * 100, 1)
        store_name = _cheapest_store_name(session, product.id, current_price)
        deals.append(DealItem(
            product_id=product.id, name_canonical=product.name_canonical,
            current_min_price=current_price, previous_min_price=previous_price,
            drop_percentage=drop_percentage, compared_to=compared_to, store_name=store_name,
        ))

    deals.sort(key=lambda d: d.drop_percentage, reverse=True)
    return deals[:filters.limit]

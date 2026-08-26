"""Lógica de negocio de `GET /products` -- función de Python normal, sin
saber que existe HTTP (ver docs/estandares-implementacion-api.md, sección
2): recibe una `Session` y un `ProductFilters`, devuelve un `Page` ya
listo para servir.

Solo cuentan los `store_product` con `match_status = confirmed` para
`minPrice`/`storeCount`/`anyInStock` (docs/api-endpoints-v1.md, sección 1)
-- un `needsReview` no debe aparecer como si el vínculo ya estuviera
confirmado. Consecuencia deliberada: un `product` sin NINGÚN
`store_product` confirmado todavía no aparece en absoluto en este listado
(INNER JOIN, no LEFT JOIN) -- mismo criterio que el 404 de
`GET /products/{id}` cuando no hay ningún listado confirmado: un canónico
recién sembrado, esperando matching, no es todavía algo que comparar
precios."""

from __future__ import annotations

from sqlalchemy import func
from sqlmodel import Session, select

from models.category import Category
from models.game import Game
from models.product import Product
from models.store_product import MatchStatus, StockStatus, StoreProduct
from schemas.common import Page, PageMeta
from schemas.products import ProductFilters, ProductSummary

# Umbral de similitud pg_trgm para el texto libre de `q` -- el mismo
# operador de similitud que ya usa matcher.py en store_monitor/, con un
# umbral más permisivo (0.2 vs 0.35/0.6 del matching automático) porque
# aquí el objetivo es "no dejar fuera nada plausible de un buscador", no
# decidir un match automático.
SEARCH_SIMILARITY_THRESHOLD = 0.2


def _confirmed_aggregates_subquery():
    """Por producto: precio mínimo, nº de tiendas distintas, y si alguna
    tiene stock -- agregado SOLO sobre store_product confirmados."""
    return (
        select(
            StoreProduct.product_id.label("product_id"),
            func.min(StoreProduct.current_price).label("min_price"),
            func.count(func.distinct(StoreProduct.store_id)).label("store_count"),
            func.bool_or(StoreProduct.stock_status == StockStatus.DISPONIBLE).label("any_in_stock"),
        )
        .where(StoreProduct.match_status == MatchStatus.CONFIRMED)
        .group_by(StoreProduct.product_id)
        .subquery()
    )


def search(session: Session, filters: ProductFilters) -> Page[ProductSummary]:
    aggregates = _confirmed_aggregates_subquery()

    query = (
        select(Product, Game.slug, Category.slug, aggregates.c.min_price,
               aggregates.c.store_count, aggregates.c.any_in_stock)
        .join(aggregates, aggregates.c.product_id == Product.id)
        .join(Game, Game.id == Product.game_id)
        .join(Category, Category.id == Product.category_id)
    )

    if filters.game:
        query = query.where(Game.slug == filters.game)
    if filters.category:
        query = query.where(Category.slug == filters.category)
    if filters.set_code:
        query = query.where(Product.set_code == filters.set_code)
    if filters.language:
        query = query.where(Product.language == filters.language)
    if filters.min_price is not None:
        query = query.where(aggregates.c.min_price >= filters.min_price)
    if filters.max_price is not None:
        query = query.where(aggregates.c.min_price <= filters.max_price)
    if filters.is_hot is not None:
        query = query.where(Product.is_hot == filters.is_hot)
    if filters.q:
        similarity = func.similarity(Product.name_canonical, filters.q)
        query = query.where(similarity > SEARCH_SIMILARITY_THRESHOLD).order_by(similarity.desc())
    else:
        query = query.order_by(Product.id)

    total = session.exec(select(func.count()).select_from(query.subquery())).one()

    page_query = query.offset((filters.page - 1) * filters.limit).limit(filters.limit)
    rows = session.exec(page_query).all()

    data = [
        ProductSummary(
            id=product.id,
            name_canonical=product.name_canonical,
            game=game_slug,
            category=category_slug,
            set_code=product.set_code,
            language=product.language,
            min_price=float(min_price),
            store_count=store_count,
            any_in_stock=any_in_stock,
        )
        for product, game_slug, category_slug, min_price, store_count, any_in_stock in rows
    ]

    return Page(data=data, meta=PageMeta(page=filters.page, limit=filters.limit, total=total))

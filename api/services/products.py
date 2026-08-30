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

from errors import ConflictError, NotFoundError
from models.category import Category
from models.game import Game
from models.price_history import PriceHistory
from models.product import Product
from models.store import Store
from models.store_product import MatchStatus, StockStatus, StoreProduct
from schemas.common import Page, PageMeta
from schemas.products import (
    Listing,
    PriceHistoryPoint,
    PriceHistorySeries,
    ProductCreate,
    ProductDetail,
    ProductFilters,
    ProductPatch,
    ProductSummary,
)

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
            packaging=product.packaging,
            min_price=float(min_price) if min_price is not None else None,
            store_count=store_count,
            any_in_stock=any_in_stock,
        )
        for product, game_slug, category_slug, min_price, store_count, any_in_stock in rows
    ]

    return Page(data=data, meta=PageMeta(page=filters.page, limit=filters.limit, total=total))


def get_by_id(session: Session, product_id: int) -> ProductDetail:
    """404 si el product no existe O si existe pero no tiene NINGÚN
    store_product confirmado todavía -- desde fuera son indistinguibles
    (docs/api-endpoints-v1.md, resuelto 2026-08-27): un canónico recién
    sembrado esperando matching no es todavía una ficha que enseñar."""
    product = session.get(Product, product_id)
    if product is None:
        raise NotFoundError(f"No existe el producto {product_id}")

    rows = session.exec(
        select(StoreProduct, Store.name)
        .join(Store, Store.id == StoreProduct.store_id)
        .where(StoreProduct.product_id == product_id, StoreProduct.match_status == MatchStatus.CONFIRMED)
        .order_by(StoreProduct.current_price.asc().nulls_last())
    ).all()
    if not rows:
        raise NotFoundError(f"No existe el producto {product_id}")

    game_slug = session.exec(select(Game.slug).where(Game.id == product.game_id)).one()
    category_slug = session.exec(select(Category.slug).where(Category.id == product.category_id)).one()

    listings = [
        Listing(store_id=sp.store_id, store_name=store_name, price=sp.current_price,
                stock_status=sp.stock_status, url=sp.store_url, last_checked_at=sp.last_checked_at)
        for sp, store_name in rows
    ]

    return ProductDetail(
        id=product.id, name_canonical=product.name_canonical, game=game_slug, category=category_slug,
        set_code=product.set_code, main_set=product.main_set, language=product.language,
        packaging=product.packaging, listings=listings,
    )


def get_price_history(session: Session, product_id: int, store_id: int | None) -> PriceHistorySeries:
    """storeId=None: agrega el precio mínimo entre tiendas CONFIRMADAS por
    día -- stockStatus del día es 'disponible' si ALGUNA tienda lo tenía
    disponible ese día, no un agregado más fino (no hay una noción
    razonable de "stock medio" entre tiendas, docs/api-endpoints-v1.md).
    storeId=<id>: la curva de esa tienda concreta (aún exige match
    confirmado -- el histórico de un store_product que dejó de estar
    confirmado no es información fiable sobre ESTE producto canónico)."""
    if session.get(Product, product_id) is None:
        raise NotFoundError(f"No existe el producto {product_id}")

    confirmed_for_product = (
        (StoreProduct.product_id == product_id) & (StoreProduct.match_status == MatchStatus.CONFIRMED)
    )

    if store_id is not None:
        rows = session.exec(
            select(PriceHistory.scraped_date, PriceHistory.price, PriceHistory.stock_status)
            .join(StoreProduct, StoreProduct.id == PriceHistory.store_product_id)
            .where(confirmed_for_product, StoreProduct.store_id == store_id)
            .order_by(PriceHistory.scraped_date)
        ).all()
        points = [PriceHistoryPoint(date=d, min_price=float(p) if p is not None else None, stock_status=s)
                  for d, p, s in rows]
    else:
        rows = session.exec(
            select(
                PriceHistory.scraped_date,
                func.min(PriceHistory.price).label("min_price"),
                func.bool_or(PriceHistory.stock_status == StockStatus.DISPONIBLE).label("any_in_stock"),
            )
            .join(StoreProduct, StoreProduct.id == PriceHistory.store_product_id)
            .where(confirmed_for_product)
            .group_by(PriceHistory.scraped_date)
            .order_by(PriceHistory.scraped_date)
        ).all()
        points = [
            PriceHistoryPoint(
                date=d, min_price=float(p) if p is not None else None,
                stock_status=StockStatus.DISPONIBLE if any_in_stock else StockStatus.AGOTADO,
            )
            for d, p, any_in_stock in rows
        ]

    return PriceHistorySeries(product_id=product_id, store_id=store_id, series=points)


def get_raw(session: Session, product_id: int) -> Product:
    """Producto canónico "tal cual" en BBDD, sin exigir ningún listing
    confirmado -- a diferencia de get_by_id() (pensado para la ficha
    pública, 404 sin listings). Usado por el formulario de edición del
    panel de administración: un producto recién creado, todavía sin
    ningún match confirmado, tiene que poder editarse igual."""
    product = session.get(Product, product_id)
    if product is None:
        raise NotFoundError(f"No existe el producto {product_id}")
    return product


def _to_detail_without_listings(session: Session, product: Product) -> ProductDetail:
    """Usado por create_product/patch_product: el `product` recién creado/
    editado puede no tener NINGÚN store_product confirmado todavía (de
    hecho nunca lo tiene justo al crearlo), así que no se puede reutilizar
    get_by_id() (ese SÍ exige listings confirmados, 404 si no hay --
    correcto para el buscador público, no para una respuesta de
    administración que solo confirma qué se acaba de guardar)."""
    game_slug = session.exec(select(Game.slug).where(Game.id == product.game_id)).one()
    category_slug = session.exec(select(Category.slug).where(Category.id == product.category_id)).one()
    return ProductDetail(
        id=product.id, name_canonical=product.name_canonical, game=game_slug, category=category_slug,
        set_code=product.set_code, main_set=product.main_set, language=product.language,
        packaging=product.packaging, listings=[],
    )


def create_product(session: Session, data: ProductCreate) -> ProductDetail:
    """409 si ya existe un product con el mismo gameId + nameCanonical --
    mismo criterio de idempotencia que ya usa seed_official_catalog.py."""
    existing = session.exec(
        select(Product).where(Product.game_id == data.game_id, Product.name_canonical == data.name_canonical)
    ).first()
    if existing is not None:
        raise ConflictError(f"Ya existe un producto '{data.name_canonical}' para gameId={data.game_id}")

    product = Product(**data.model_dump())
    session.add(product)
    session.commit()
    session.refresh(product)
    return _to_detail_without_listings(session, product)


def patch_product(session: Session, product_id: int, patch: ProductPatch) -> ProductDetail:
    product = session.get(Product, product_id)
    if product is None:
        raise NotFoundError(f"No existe el producto {product_id}")

    updates = patch.model_dump(exclude_unset=True)
    if "name_canonical" in updates:
        collision = session.exec(
            select(Product).where(
                Product.game_id == product.game_id,
                Product.name_canonical == updates["name_canonical"],
                Product.id != product_id,
            )
        ).first()
        if collision is not None:
            raise ConflictError(f"Ya existe un producto '{updates['name_canonical']}' para este juego")

    for field, value in updates.items():
        setattr(product, field, value)

    session.add(product)
    session.commit()
    session.refresh(product)
    return _to_detail_without_listings(session, product)

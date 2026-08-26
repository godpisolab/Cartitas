"""Router de `GET /products` -- fino a propósito: parsea query params,
delega en `services/products.py`, pone el `Link` header. Ningún `select`
ni regla de negocio vive aquí (ver docs/estandares-implementacion-api.md,
sección 2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlmodel import Session

import services.products as products_service
from auth import require_scope
from db import get_session
from models.product import ProductLanguage
from pagination import MAX_LIMIT, build_link_header
from schemas.common import Page
from schemas.products import (
    PriceHistorySeries,
    ProductCreate,
    ProductDetail,
    ProductFilters,
    ProductPatch,
    ProductSummary,
)

router = APIRouter()


@router.get("/products", response_model=Page[ProductSummary])
def list_products(
    request: Request,
    response: Response,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    q: str | None = Query(default=None),
    game: str | None = Query(default=None),
    category: str | None = Query(default=None),
    setCode: str | None = Query(default=None),
    language: ProductLanguage | None = Query(default=None),
    minPrice: float | None = Query(default=None),
    maxPrice: float | None = Query(default=None),
    isHot: bool | None = Query(default=None),
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read")),
) -> Page[ProductSummary]:
    filters = ProductFilters(
        q=q, game=game, category=category, set_code=setCode, language=language,
        min_price=minPrice, max_price=maxPrice, is_hot=isHot,
        page=page, limit=limit,
    )
    result = products_service.search(session, filters)

    link = build_link_header(request, page=page, limit=limit, total=result.meta.total)
    if link:
        response.headers["Link"] = link

    return result


@router.get("/products/{product_id}", response_model=ProductDetail)
def get_product(
    product_id: int,
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read")),
) -> ProductDetail:
    return products_service.get_by_id(session, product_id)


@router.get("/products/{product_id}/price-history", response_model=PriceHistorySeries)
def get_product_price_history(
    product_id: int,
    storeId: int | None = Query(default=None),
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("read")),
) -> PriceHistorySeries:
    return products_service.get_price_history(session, product_id, storeId)


@router.post("/products", response_model=ProductDetail, status_code=201)
def create_product(
    body: ProductCreate,
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("admin:*")),
) -> ProductDetail:
    return products_service.create_product(session, body)


@router.patch("/products/{product_id}", response_model=ProductDetail)
def patch_product(
    product_id: int,
    body: ProductPatch,
    session: Session = Depends(get_session),
    _: None = Depends(require_scope("admin:*")),
) -> ProductDetail:
    return products_service.patch_product(session, product_id, body)

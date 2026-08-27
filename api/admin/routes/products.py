"""Rutas HTML del panel de productos -- listado, alta, edición. Llama a
`services/products.py` y `services/catalog.py` DIRECTAMENTE, mismo patrón
que `admin/routes/matches.py` (docs/estandares-implementacion-frontend.md
sección 2.3). A diferencia del panel de matching, aquí no hay htmx --
alta/edición son formularios de página completa (patrón POST-Redirect-GET),
más apropiado para un formulario largo que un intercambio de fila."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

import services.catalog as catalog_service
import services.products as products_service
from admin.templates_env import templates
from db import get_session
from errors import ConflictError
from models.product import ProductLanguage
from schemas.products import ProductCreate, ProductFilters, ProductPatch
from shared.classify import PRODUCT_TYPE_TO_CATEGORY_SLUG

router = APIRouter()


def _catalog_context(session: Session) -> dict:
    return {
        "games": catalog_service.list_games(session),
        "categories": catalog_service.list_categories_flat(session),
        "languages": list(ProductLanguage),
    }


@router.get("/products")
def list_products(
    request: Request,
    q: str | None = Query(default=None),
    page: int = Query(1, ge=1),
    session: Session = Depends(get_session),
):
    result = products_service.search(session, ProductFilters(q=q, page=page, limit=50))
    return templates.TemplateResponse(request, "products/list.html", {"items": result.data, "q": q or ""})


@router.get("/products/new")
def new_product_form(
    request: Request,
    productType: str | None = Query(default=None),
    mainSet: str | None = Query(default=None),
    language: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    # Prellenado desde el enlace "Crear canónico" de missing-candidates
    # (docs/plan-cierre-panel-gestor.md sección 1.3): productType llega como
    # el código de shared.classify (p.ej. "BOOSTER_BOX"), hay que resolverlo
    # a category_id vía el mismo mapeo que ya usa services/matches.py.
    category_slug = PRODUCT_TYPE_TO_CATEGORY_SLUG.get(productType) if productType else None
    categories = catalog_service.list_categories_flat(session)
    selected_category_id = next((c.id for c in categories if c.slug == category_slug), None)

    context = _catalog_context(session)
    context.update({
        "mode": "create", "action": "/admin/products", "error": None,
        "selected_category_id": selected_category_id, "selected_language": language or "",
        "name_canonical": "", "set_code": "", "main_set": mainSet or "", "image_url": "",
        "is_hot": False, "hot_until": "",
    })
    return templates.TemplateResponse(request, "products/form.html", context)


@router.post("/products")
def create_product(
    request: Request,
    game_id: int = Form(...),
    category_id: int = Form(...),
    set_code: str = Form(""),
    main_set: str = Form(""),
    language: str = Form(""),
    name_canonical: str = Form(...),
    image_url: str = Form(""),
    is_hot: bool = Form(False),
    session: Session = Depends(get_session),
):
    data = ProductCreate(
        game_id=game_id, category_id=category_id, set_code=set_code or None, main_set=main_set or None,
        language=language or None, name_canonical=name_canonical, image_url=image_url or None, is_hot=is_hot,
    )
    try:
        product = products_service.create_product(session, data)
    except ConflictError as e:
        # 2.5: un 409 no revienta en la página de error de FastAPI -- se
        # vuelve a mostrar el formulario con lo ya escrito, más el motivo.
        context = _catalog_context(session)
        context.update({
            "mode": "create", "action": "/admin/products", "error": e.detail,
            "selected_category_id": category_id, "selected_language": language,
            "name_canonical": name_canonical, "set_code": set_code, "main_set": main_set,
            "image_url": image_url, "is_hot": is_hot, "hot_until": "",
        })
        return templates.TemplateResponse(request, "products/form.html", context, status_code=409)

    return RedirectResponse(f"/admin/products/{product.id}/edit", status_code=303)


@router.get("/products/{product_id}/edit")
def edit_product_form(request: Request, product_id: int, session: Session = Depends(get_session)):
    product = products_service.get_raw(session, product_id)
    context = _catalog_context(session)
    context.update({
        "mode": "edit", "action": f"/admin/products/{product_id}", "error": None,
        "selected_category_id": product.category_id,
        "selected_language": product.language or "",
        "name_canonical": product.name_canonical, "set_code": product.set_code or "",
        "main_set": product.main_set or "", "image_url": product.image_url or "",
        "is_hot": product.is_hot, "hot_until": product.hot_until.isoformat() if product.hot_until else "",
        "product": product,
    })
    return templates.TemplateResponse(request, "products/form.html", context)


@router.post("/products/{product_id}")
def update_product(
    request: Request,
    product_id: int,
    name_canonical: str = Form(...),
    image_url: str = Form(""),
    is_hot: bool = Form(False),
    hot_until: date | None = Form(None),
    session: Session = Depends(get_session),
):
    patch = ProductPatch(name_canonical=name_canonical, image_url=image_url or None, is_hot=is_hot,
                          hot_until=hot_until)
    try:
        product = products_service.patch_product(session, product_id, patch)
    except ConflictError as e:
        raw = products_service.get_raw(session, product_id)
        context = _catalog_context(session)
        context.update({
            "mode": "edit", "action": f"/admin/products/{product_id}", "error": e.detail,
            "selected_category_id": raw.category_id,
            "selected_language": raw.language or "",
            "name_canonical": name_canonical, "set_code": raw.set_code or "", "main_set": raw.main_set or "",
            "image_url": image_url, "is_hot": is_hot,
            "hot_until": hot_until.isoformat() if hot_until else "",
            "product": raw,
        })
        return templates.TemplateResponse(request, "products/form.html", context, status_code=409)

    return RedirectResponse(f"/admin/products/{product_id}/edit", status_code=303)

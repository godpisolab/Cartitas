"""Lógica del panel de matching -- docs/api-endpoints-v1.md sección 6 y
docs/api-endpoints-gestor.md sección 1.

ALERTA DE DISEÑO #1 (ya resuelta en el documento): `minSimilarity`/
`maxSimilarity` filtran sobre el score del MEJOR CANDIDATO calculado en
caliente (misma consulta que el top-3), nunca sobre `matchConfidence` --
esa columna es siempre NULL para todo lo que aparece en esta cola
(needsReview/unmatched), `matcher._evaluate()` solo la rellena cuando el
resultado ya es `confirmed`.

Consecuencia práctica de lo anterior: derivar la categoría de un
store_product exige `classify_product()` en Python (igual que ya hace
matcher.py) antes de poder consultar candidatos por categoría -- no es
expresable como una única query SQL. Por eso esta función trae TODAS las
filas que cumplen los filtros baratos (status/storeId/reviewedAt) sin
paginar en SQL, calcula clasificación+candidatos por fila en Python, y
pagina la lista ya filtrada -- mismo patrón que ya usa
matcher.run_matching() para el mismo problema. Aceptable para una cola de
administración (cientos de filas, no millones), no para un listado
público de alto tráfico."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_
from sqlmodel import Session, select

from shared.classify import (
    NOT_APPLICABLE_PRODUCT_TYPES,
    PRODUCT_TYPE_TO_CATEGORY_SLUG,
    classify_product,
    classify_with_category,
    is_box_variant,
)
from errors import ConflictError, NotFoundError, UnprocessableEntityError
from models.category import Category
from models.product import Product
from models.store import Store
from models.store_product import MatchStatus, StoreProduct
from schemas.common import Page, PageMeta
from schemas.matches import (
    STATUS_FILTER_TO_ENUM,
    REVIEWED_HIDE_DAYS,
    ConfirmBody,
    MatchCandidate,
    MatchFilters,
    MatchItem,
    MissingCandidateItem,
    RejectBody,
    StoreRef,
)

TOP_CANDIDATES_LIMIT = 3


def _category_id_for_slug(session: Session, slug: str | None) -> int | None:
    if slug is None:
        return None
    return session.exec(select(Category.id).where(Category.slug == slug)).first()


def _top_candidates(
    session: Session, category_id: int | None, raw_name: str,
    set_code: str | None = None, language: str | None = None,
) -> list[MatchCandidate]:
    """Top-3 por similarity, con el mismo desempate por set_code exacto que
    ya usa store_monitor/matcher.py::_best_candidate() (revisión de la cola
    de matching, 2026-08-27): varios raw_names de la misma familia comparten
    casi todo el texto salvo el código (ej. "Starter Deck ONE PIECE FILM
    edition ST-05", reutilizado como plantilla, gana en similarity() pura a
    la variante concreta correcta) -- cuando el raw_name trae un código
    reconocible, el candidato de ESE código sale primero en la lista que ve
    quien revisa, no enterrado en el puesto #2 o #3.

    Desempate por IDIOMA EXACTO (2026-08-28, mismo hallazgo que
    matcher._best_candidate): un set_code trae casi siempre un canónico EN
    y otro JP con texto casi idéntico, así que similarity() los deja
    prácticamente empatados -- sin esto, el candidato #1 que ve quien
    revisa puede ser el idioma que NO coincide con lo que la tienda
    declaró, y es justo el que _evaluate() usaría para decidir (mismo
    criterio de orden en los dos sitios, a propósito). Va DESPUÉS de
    set_code y ANTES de caja/sobre -- mismo razonamiento que en
    matcher.py: idioma distinto es un producto distinto, caja/sobre es una
    distinción más fina dentro del mismo producto."""
    if category_id is None:
        return []
    similarity = func.similarity(Product.name_canonical, raw_name)
    order_by = [similarity.desc()]

    # Desempate por CAJA/SOBRE (is_box_variant): dentro de una MISMA
    # categoría+set_code puede convivir la variante caja y la de un sobre
    # suelto ("Premium Booster"/"Premium Booster Box", ambas PRB-02, misma
    # categoría premium-collection) -- antes de esta señal, similarity()
    # podía preferir la variante equivocada.
    # Solo se comprueba "box" en el lado del candidato (no "caja") --
    # name_canonical está siempre en inglés (verificado: 0 filas con "caja"
    # en el catálogo), a diferencia de raw_name que sí puede venir en
    # español y por eso is_box_variant() comprueba ambas palabras ahí.
    is_box = is_box_variant(raw_name)
    if is_box is not None:
        box_match = case(
            (Product.name_canonical.ilike("%box%") == is_box, 1),
            else_=0,
        )
        order_by.insert(0, box_match.desc())

    if language:
        # CASE explícito por el mismo motivo que set_code_match más abajo:
        # trata NULL/idioma no coincidente como "no cumple", sin colar
        # candidatos de idioma desconocido por delante de uno correcto.
        language_match = case((Product.language == language, 1), else_=0)
        order_by.insert(0, language_match.desc())

    if set_code:
        # CASE explícito, no (Product.set_code == set_code).desc() a secas:
        # Postgres ordena NULL PRIMERO en DESC por defecto -- con la
        # comparación cruda, los candidatos SIN set_code (accesorios,
        # playmats...) se colarían delante del que sí coincide de verdad.
        # CASE WHEN trata NULL como "no cumple" (igual que FALSE), no como
        # un tercer valor que ordenar aparte.
        set_code_match = case((Product.set_code == set_code, 1), else_=0)
        order_by.insert(0, set_code_match.desc())
    rows = session.exec(
        select(Product.id, Product.name_canonical, Product.set_code, similarity.label("score"))
        .where(Product.category_id == category_id)
        .order_by(*order_by)
        .limit(TOP_CANDIDATES_LIMIT)
    ).all()

    # Fallback cross-categoría por set_code exacto (2026-08-27, mismo caso
    # PRB02 que matcher._best_candidate): si ningún candidato de la
    # categoría derivada trae el set_code exacto, se añade también el
    # mejor candidato de TODO el catálogo con ese set_code -- para que la
    # cola de revisión muestre el canónico correcto aunque la tienda lo
    # haya clasificado en una categoría distinta a como se sembró.
    if set_code and not any(sc == set_code for _, _, sc, _ in rows):
        fallback_order_by = [similarity.desc()]
        if language:
            fallback_order_by.insert(0, case((Product.language == language, 1), else_=0).desc())
        cross_category_row = session.exec(
            select(Product.id, Product.name_canonical, Product.set_code, similarity.label("score"))
            .where(Product.set_code == set_code)
            .order_by(*fallback_order_by)
            .limit(1)
        ).first()
        if cross_category_row is not None:
            rows = [cross_category_row, *rows][:TOP_CANDIDATES_LIMIT]

    return [
        MatchCandidate(product_id=pid, name_canonical=name, similarity=float(score))
        for pid, name, _set_code, score in rows
    ]


def _candidates_for(
    session: Session, raw_name: str, raw_variant: str | None, raw_tags: str | None = None,
) -> list[MatchCandidate]:
    classification, category_slug = classify_with_category(raw_name, raw_variant, raw_tags)
    category_id = _category_id_for_slug(session, category_slug)
    return _top_candidates(session, category_id, raw_name, classification.set_code, classification.language)


def _to_item(store_product: StoreProduct, store: Store, candidates: list[MatchCandidate]) -> MatchItem:
    return MatchItem(
        store_product_id=store_product.id, store=StoreRef(id=store.id, name=store.name),
        store_url=store_product.store_url,
        raw_name=store_product.raw_name, raw_variant=store_product.raw_variant,
        current_price=store_product.current_price, stock_status=store_product.stock_status,
        match_status=store_product.match_status, reviewed_at=store_product.reviewed_at,
        reviewed_reason=store_product.reviewed_reason,
        candidates=candidates, product_id=store_product.product_id,
        match_confidence=store_product.match_confidence,
    )


def _get_or_404(session: Session, store_product_id: int) -> StoreProduct:
    store_product = session.get(StoreProduct, store_product_id)
    if store_product is None:
        raise NotFoundError(f"No existe store_product {store_product_id}")
    return store_product


def list_matches(session: Session, filters: MatchFilters) -> Page[MatchItem]:
    query = select(StoreProduct, Store).join(Store, Store.id == StoreProduct.store_id)

    if filters.status == "all":
        query = query.where(StoreProduct.match_status != MatchStatus.NOT_APPLICABLE)
    else:
        query = query.where(StoreProduct.match_status == STATUS_FILTER_TO_ENUM[filters.status])

    if filters.store_id is not None:
        query = query.where(StoreProduct.store_id == filters.store_id)

    if not filters.include_reviewed:
        cutoff = datetime.now(timezone.utc) - timedelta(days=REVIEWED_HIDE_DAYS)
        query = query.where(or_(StoreProduct.reviewed_at.is_(None), StoreProduct.reviewed_at < cutoff))

    rows = session.exec(query.order_by(StoreProduct.id)).all()

    items: list[MatchItem] = []
    for store_product, store in rows:
        if store_product.match_status == MatchStatus.CONFIRMED:
            # No se calcula el top-3 para algo ya resuelto -- trabajo de
            # BBDD desperdiciado (docs/api-endpoints-gestor.md sección 1).
            items.append(_to_item(store_product, store, candidates=[]))
            continue

        candidates = _candidates_for(session, store_product.raw_name, store_product.raw_variant, store_product.raw_tags)
        best_score = candidates[0].similarity if candidates else None

        if filters.min_similarity is not None and (best_score is None or best_score < filters.min_similarity):
            continue
        if filters.max_similarity is not None and (best_score is None or best_score > filters.max_similarity):
            continue

        items.append(_to_item(store_product, store, candidates))

    total = len(items)
    start = (filters.page - 1) * filters.limit
    page_items = items[start:start + filters.limit]
    return Page(data=page_items, meta=PageMeta(page=filters.page, limit=filters.limit, total=total))


def confirm_match(session: Session, store_product_id: int, body: ConfirmBody) -> MatchItem:
    store_product = _get_or_404(session, store_product_id)
    if session.get(Product, body.product_id) is None:
        raise UnprocessableEntityError(f"No existe el producto {body.product_id}")

    # matchConfidence solo si productId coincide con uno de los candidates
    # VIGENTES al confirmar (docs/api-endpoints-v1.md sección 6) -- permite
    # distinguir después "el algoritmo ya acertaba" de "elección manual
    # pura", útil para calibrar los umbrales de C.2 con datos reales.
    candidates = _candidates_for(session, store_product.raw_name, store_product.raw_variant, store_product.raw_tags)
    matching_candidate = next((c for c in candidates if c.product_id == body.product_id), None)

    store_product.match_status = MatchStatus.CONFIRMED
    store_product.product_id = body.product_id
    store_product.match_confidence = matching_candidate.similarity if matching_candidate else None
    session.add(store_product)
    session.commit()
    session.refresh(store_product)

    store = session.get(Store, store_product.store_id)
    return _to_item(store_product, store, candidates=[])


def reject_match(session: Session, store_product_id: int, body: RejectBody) -> MatchItem:
    store_product = _get_or_404(session, store_product_id)

    store_product.match_status = STATUS_FILTER_TO_ENUM[body.mark_as]
    store_product.reviewed_at = datetime.now(timezone.utc)
    store_product.reviewed_reason = body.reason
    store_product.product_id = None
    session.add(store_product)
    session.commit()
    session.refresh(store_product)

    store = session.get(Store, store_product.store_id)
    return _to_item(store_product, store, candidates=[])


def reopen_match(session: Session, store_product_id: int) -> MatchItem:
    """Deshace una confirmación equivocada -- parte de matchStatus=confirmed
    (a diferencia de reject, que parte de uno pendiente). 409 si no estaba
    confirmado: no tiene sentido "reabrir" algo que no estaba cerrado."""
    store_product = _get_or_404(session, store_product_id)
    if store_product.match_status != MatchStatus.CONFIRMED:
        raise ConflictError("Solo se puede reabrir un match confirmado")

    store_product.match_status = MatchStatus.NEEDS_REVIEW
    store_product.product_id = None
    store_product.match_confidence = None
    store_product.reviewed_at = None  # limpia también el rechazo si lo hubiera habido antes
    store_product.reviewed_reason = None
    session.add(store_product)
    session.commit()
    session.refresh(store_product)

    store = session.get(Store, store_product.store_id)
    candidates = _candidates_for(session, store_product.raw_name, store_product.raw_variant, store_product.raw_tags)
    return _to_item(store_product, store, candidates)


def missing_candidates(session: Session, min_stores: int) -> list[MissingCandidateItem]:
    """Puerto de matcher.find_missing_canonical_candidates() (C.1) sobre
    SQLModel -- agrupa lo NO confirmado por (product_type, set_code,
    language) derivados de raw_name/raw_variant/raw_tags, e ignora
    combinaciones donde ya existe un product candidato en esa
    categoría+set_code.

    set_code, no main_set (2026-08-27, mismo motivo que
    matcher.find_missing_canonical_candidates): Double Pack e Illustration
    Box tienen set_code propio pero main_set=NULL en sus canónicos --
    agrupar/comprobar por main_set generaba falsos positivos. main_set se
    sigue derivando por grupo solo para prellenar el formulario de alta
    (sección 1.3), nunca para decidir si ya existe candidato."""
    rows = session.exec(
        select(StoreProduct.store_id, StoreProduct.raw_name, StoreProduct.raw_variant, StoreProduct.raw_tags)
        .where(StoreProduct.match_status != MatchStatus.CONFIRMED)
    ).all()

    groups: dict[tuple, set[int]] = {}
    main_sets: dict[tuple, str | None] = {}
    for store_id, raw_name, raw_variant, raw_tags in rows:
        classification = classify_product(raw_name, raw_variant, raw_tags)
        if classification.product_type in NOT_APPLICABLE_PRODUCT_TYPES:
            continue
        key = (classification.product_type, classification.set_code, classification.language)
        groups.setdefault(key, set()).add(store_id)
        main_sets[key] = classification.main_set

    suggestions = []
    for (product_type, set_code, language), store_ids in groups.items():
        if len(store_ids) < min_stores:
            continue

        category_slug = PRODUCT_TYPE_TO_CATEGORY_SLUG.get(product_type)
        category_id = _category_id_for_slug(session, category_slug)
        has_candidate = False
        if category_id is not None:
            has_candidate = session.exec(
                select(Product.id).where(
                    Product.category_id == category_id, Product.set_code.is_not_distinct_from(set_code),
                )
            ).first() is not None

        # Mismo fallback cross-categoría que _top_candidates()/matcher.py
        # (caso PRB02): un candidato con ese set_code exacto en OTRA
        # categoría no es un hueco real del catálogo.
        if not has_candidate and set_code is not None:
            has_candidate = session.exec(
                select(Product.id).where(Product.set_code == set_code)
            ).first() is not None

        if not has_candidate:
            suggestions.append(MissingCandidateItem(
                product_type=product_type, set_code=set_code,
                main_set=main_sets[(product_type, set_code, language)],
                language=language, store_count=len(store_ids),
            ))

    return sorted(suggestions, key=lambda s: s.store_count, reverse=True)

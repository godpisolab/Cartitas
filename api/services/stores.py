"""Lógica de tiendas -- lectura para el catálogo/panel, y edición parcial
de administración (PATCH /stores/{id})."""

from __future__ import annotations

from sqlmodel import Session, select

from errors import NotFoundError
from models.store import Store
from schemas.stores import StoreDetail, StorePatch, StoreSummary


def _to_summary(store: Store) -> StoreSummary:
    return StoreSummary(id=store.id, name=store.name, website_url=store.website_url,
                         platform=store.platform, active=store.active)


def _to_detail(store: Store) -> StoreDetail:
    return StoreDetail(
        **_to_summary(store).model_dump(),
        last_scraped_at=store.last_scraped_at,
        crawl_delay_seconds=store.crawl_delay_seconds,
        disallowed=store.disallowed,
        consecutive_failures=store.consecutive_failures,
        backoff_until=store.backoff_until,
    )


def list_stores(session: Session) -> list[StoreSummary]:
    stores = session.exec(select(Store).order_by(Store.name)).all()
    return [_to_summary(s) for s in stores]


def _get_or_404(session: Session, store_id: int) -> Store:
    store = session.get(Store, store_id)
    if store is None:
        raise NotFoundError(f"No existe la tienda {store_id}")
    return store


def get_store(session: Session, store_id: int) -> StoreDetail:
    return _to_detail(_get_or_404(session, store_id))


def patch_store(session: Session, store_id: int, patch: StorePatch) -> StoreDetail:
    store = _get_or_404(session, store_id)

    updates = patch.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(store, field, value)

    session.add(store)
    session.commit()
    session.refresh(store)
    return _to_detail(store)

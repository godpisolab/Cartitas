"""Lógica de `GET /games` y `GET /categories` -- listas pequeñas y de baja
frecuencia de cambio, sin paginación (docs/api-endpoints-v1.md sección 4)."""

from __future__ import annotations

from sqlmodel import Session, select

from models.category import Category
from models.game import Game
from schemas.catalog import CategoryNode, GameItem


def list_games(session: Session) -> list[GameItem]:
    games = session.exec(select(Game).order_by(Game.name)).all()
    return [GameItem(id=g.id, name=g.name, slug=g.slug) for g in games]


def list_categories_flat(session: Session) -> list[Category]:
    """Lista plana (no el árbol de dos niveles de list_categories(), pensado
    para el catálogo de filtros público) -- para un <select> de formulario
    de administración basta con id/name/slug sin jerarquía."""
    return session.exec(select(Category).order_by(Category.name)).all()


def list_categories(session: Session) -> list[CategoryNode]:
    """Árbol de dos niveles (padre -> hijos). `Lote de cartas`/`Otros` (D.3)
    nunca aparecen porque nunca se siembran en `category` -- no hace falta
    filtrarlos aquí."""
    categories = session.exec(select(Category).order_by(Category.name)).all()

    nodes_by_id = {c.id: CategoryNode(id=c.id, name=c.name, slug=c.slug, children=[]) for c in categories}
    roots: list[CategoryNode] = []

    for category in categories:
        node = nodes_by_id[category.id]
        if category.parent_category_id is None:
            roots.append(node)
        else:
            parent = nodes_by_id.get(category.parent_category_id)
            if parent is not None:
                parent.children.append(node)

    return roots

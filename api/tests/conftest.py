"""Fixtures compartidas para los tests de integración contra Postgres real.
Reutiliza la MISMA `cartitas_test` que store_monitor/tests/conftest.py
(mismo contenedor, mismo esquema aplicado) -- no hace falta una base de
datos de test separada por servicio.

Cada test parte de tablas vacías (TRUNCATE, no rollback de transacción):
igual que en store_monitor, algunos tests podrían hacer varios commits
dentro de una petición, así que un rollback exterior no sería fiable."""

from __future__ import annotations

import os

import psycopg2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, create_engine

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://cartitas:cartitas@localhost:5433/cartitas_test",
)


def _postgres_available() -> bool:
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL, connect_timeout=2)
        conn.close()
        return True
    except psycopg2.OperationalError:
        return False


@pytest.fixture(scope="session")
def postgres_available():
    if not _postgres_available():
        pytest.skip(
            f"cartitas_test no accesible en {TEST_DATABASE_URL} -- levanta el "
            f"docker-compose y aplica el esquema (ver store_monitor/tests/conftest.py)"
        )
    return True


@pytest.fixture(scope="session")
def engine(postgres_available):
    return create_engine(TEST_DATABASE_URL)


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(engine, monkeypatch):
    """TestClient con get_session() apuntando a cartitas_test -- el resto
    de la app (auth, errores, routers) corre tal cual, sin mockear nada más."""
    import db as db_module
    from main import app

    def _override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[db_module.get_session] = _override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean_db(request, engine):
    """Autouse pero no fuerza a todos los tests a necesitar Postgres: solo
    trunca si el test pidió `session` o `client` explícitamente."""
    if "session" not in request.fixturenames and "client" not in request.fixturenames:
        yield
        return

    with engine.connect() as conn:
        conn.execute(text("""
            TRUNCATE restock_event, restock_subscription, price_history,
                     store_product, product, category, game, store
            RESTART IDENTITY CASCADE
        """))
        conn.commit()
    yield


@pytest.fixture
def api_key(monkeypatch):
    """Key de test con scope 'read' -- monkeypatchea config.API_KEYS
    directamente, mismo patrón que persistence.DATABASE_URL en
    store_monitor/ (ver docstring de api/config.py)."""
    import config
    monkeypatch.setattr(config, "API_KEYS", {"test-key": frozenset({"read"})})
    return "test-key"


@pytest.fixture
def auth_headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def admin_credentials(monkeypatch):
    """Credencial de persona para el panel (admin/auth.py) -- mismo patrón
    que `api_key`, pero monkeypatcheando config.ADMIN_USERNAME/PASSWORD en
    vez de API_KEYS. Devuelve la tupla lista para `client.get(..., auth=)`."""
    import config
    monkeypatch.setattr(config, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "test-password")
    return ("admin", "test-password")


@pytest.fixture
def seed_listing(session):
    """Factory fixture: crea game/category/store bajo demanda (idempotente
    por slug/website_url dentro del mismo test) + un product con UN
    store_product -- la unidad mínima que necesita GET /products para
    mostrar algo. Devuelve el id del product creado."""

    def _get_or_create_game(slug: str) -> int:
        row = session.exec(text("SELECT id FROM game WHERE slug = :slug"), params={"slug": slug}).first()
        if row:
            return row[0]
        row = session.exec(
            text("INSERT INTO game (name, slug) VALUES (:slug, :slug) RETURNING id"), params={"slug": slug},
        ).first()
        return row[0]

    def _get_or_create_category(slug: str) -> int:
        row = session.exec(text("SELECT id FROM category WHERE slug = :slug"), params={"slug": slug}).first()
        if row:
            return row[0]
        row = session.exec(
            text("INSERT INTO category (name, slug) VALUES (:slug, :slug) RETURNING id"), params={"slug": slug},
        ).first()
        return row[0]

    def _get_or_create_store(name: str) -> int:
        website_url = f"https://{name.lower().replace(' ', '-')}.example"
        row = session.exec(
            text("SELECT id FROM store WHERE website_url = :url"), params={"url": website_url},
        ).first()
        if row:
            return row[0]
        row = session.exec(
            text("INSERT INTO store (name, website_url, platform) VALUES (:name, :url, 'shopify') RETURNING id"),
            params={"name": name, "url": website_url},
        ).first()
        return row[0]

    def _seed(
        *,
        name_canonical: str = "Booster Box OP16 EN",
        game_slug: str = "one-piece",
        category_slug: str = "booster-box",
        set_code: str | None = "OP16",
        language: str | None = "EN",
        is_hot: bool = False,
        store_name: str = "Cardzone",
        price: float = 109.90,
        stock_status: str = "disponible",
        match_status: str = "confirmed",
        product_id: int | None = None,
    ) -> int:
        """Con product_id=None (caso normal): crea un product nuevo + su
        primer store_product. Con product_id=<id de un _seed anterior>: NO
        crea un product nuevo, solo añade otra fila store_product (otra
        tienda vendiendo el MISMO canónico) -- para probar agregados entre
        varias tiendas sin depender de que dos nombres iguales colapsen en
        el mismo product (no hay esa lógica de deduplicación aquí, la haría
        seed_official_catalog.py en producción)."""
        store_id = _get_or_create_store(store_name)

        if product_id is None:
            game_id = _get_or_create_game(game_slug)
            category_id = _get_or_create_category(category_slug)
            product_id = session.exec(
                text("""
                    INSERT INTO product (game_id, category_id, set_code, main_set, language, name_canonical, is_hot)
                    VALUES (:game_id, :category_id, :set_code, :set_code, :language, :name_canonical, :is_hot)
                    RETURNING id
                """),
                params={"game_id": game_id, "category_id": category_id, "set_code": set_code,
                        "language": language, "name_canonical": name_canonical, "is_hot": is_hot},
            ).first()[0]

        _seed.counter += 1
        session.exec(
            text("""
                INSERT INTO store_product (store_id, product_id, match_status, store_url, raw_name,
                                            current_price, stock_status)
                VALUES (:store_id, :product_id, :match_status, :store_url, :raw_name, :price, :stock_status)
            """),
            params={"store_id": store_id, "product_id": product_id, "match_status": match_status,
                    # store_url debe ser único por LLAMADA, no solo por (store, product): un
                    # test puede seedear dos filas para la misma tienda+producto (dos
                    # "variantes" sin raw_variant) y UNIQUE NULLS NOT DISTINCT las trataría
                    # como el mismo listado si compartieran store_url.
                    "store_url": f"https://store{store_id}.example/p{product_id}-{_seed.counter}",
                    "raw_name": name_canonical, "price": price, "stock_status": stock_status},
        )
        session.commit()
        return product_id

    _seed.counter = 0
    return _seed

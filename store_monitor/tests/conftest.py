"""Fixtures compartidas para los tests de integración contra Postgres real
(persistence.py, store_state.py -- sección 3 y 4 del plan de pruebas).

Usa una base de datos SEPARADA (`cartitas_test`, mismo contenedor de
`docker-compose.yml`) para no mezclar datos de test con los de desarrollo.
Antes de correr estos tests:

    docker exec cartitas-postgres psql -U cartitas -d cartitas -c "CREATE DATABASE cartitas_test;"
    docker exec -i cartitas-postgres psql -U cartitas -d cartitas_test < ../schema-postgresql-app-tcg.sql

Cada test parte de tablas vacías (TRUNCATE, no una transacción con
rollback): persistence.py hace sus propios commit() internos, así que
envolver en una transacción exterior y hacer rollback al final NO aislaría
nada -- los commits internos ya habrían hecho permanentes los cambios."""

from __future__ import annotations

import os

import psycopg2
import pytest

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
            f"docker-compose y aplica el esquema (ver docstring de este conftest)"
        )
    return True


@pytest.fixture
def db_conn(postgres_available, monkeypatch):
    """Conexión a cartitas_test, con persistence.DATABASE_URL (y por tanto
    store_state.py, que importa persistence en diferido) apuntando ahí
    durante el test."""
    import persistence
    monkeypatch.setattr(persistence, "DATABASE_URL", TEST_DATABASE_URL)

    conn = psycopg2.connect(TEST_DATABASE_URL)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def clean_db(request):
    """Autouse pero NO fuerza a todos los tests a necesitar Postgres: solo
    trunca si el test pidió db_conn explícitamente (si no, no hay nada que
    limpiar y no vale la pena ni comprobar que Postgres esté arriba)."""
    if "db_conn" not in request.fixturenames:
        yield
        return

    conn = request.getfixturevalue("db_conn")
    with conn.cursor() as cur:
        cur.execute("""
            TRUNCATE restock_event, restock_subscription, price_history,
                     store_product, product, category, game, store
            RESTART IDENTITY CASCADE
        """)
    conn.commit()
    yield

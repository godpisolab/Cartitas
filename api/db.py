"""Motor de BBDD y sesión inyectable vía FastAPI Depends(). Un único engine
por proceso (SQLModel/SQLAlchemy gestiona su propio pool de conexiones) --
nada aquí sabe de HTTP ni de qué router lo usa."""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, create_engine

import config

engine = create_engine(config.DATABASE_URL)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session

"""Database setup: SQLAlchemy 2.x, SQLite for the demo, Postgres-ready schema."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = settings.DATABASE_URL
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        path = url.replace("sqlite:///", "", 1)
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args=connect_args, future=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    from . import models  # noqa: F401  (register models)

    Base.metadata.create_all(bind=engine)
    # create_all never alters existing tables; backfill the
    # prompt-injection guard columns on older databases.
    from .injection_guard import ensure_columns

    ensure_columns(engine)
    # Same backstop for the Agent identity mode columns (the
    # agent_identities table itself is created by create_all above).
    from .agent_identity import ensure_columns as ensure_identity_columns

    ensure_identity_columns(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

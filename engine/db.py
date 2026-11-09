"""Database wiring. SQLite by default; PostgreSQL via NETCFG_DATABASE_URL."""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("NETCFG_DATABASE_URL", "sqlite:///./netcfg.db")


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or DATABASE_URL
    is_sqlite = url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(url, future=True, connect_args=connect_args, pool_pre_ping=True)
    if is_sqlite and ":memory:" not in url:
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - exercised via concurrency tests
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()
    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(target_engine=None) -> None:
    from . import models  # noqa: F401  (registers tables on Base.metadata)
    from .bootstrap import ensure_append_only

    target = target_engine or engine
    Base.metadata.create_all(target)
    ensure_append_only(target)

"""Database wiring. SQLite by default; PostgreSQL via NETCFG_DATABASE_URL."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("NETCFG_DATABASE_URL", "sqlite:///./netcfg.db")


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or DATABASE_URL
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, connect_args=connect_args)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(target_engine=None) -> None:
    from . import models  # noqa: F401  (registers tables on Base.metadata)
    from .bootstrap import ensure_append_only

    target = target_engine or engine
    Base.metadata.create_all(target)
    ensure_append_only(target)

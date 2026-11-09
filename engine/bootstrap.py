"""Idempotent schema guarantees that must hold for existing databases too.

`create_all` only creates missing tables; it does not run triggers on tables
that already exist, and it will not recreate a dropped index. Call this at
startup so an older database gets the same guarantees as a fresh one.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .guards import (POSTGRES_FUNCTION, POSTGRES_TRIGGERS, POSTGRES_TRUNCATE_FUNCTION,
                     SQLITE_BASELINE_INDEX, SQLITE_TRIGGERS, BASELINE_INDEX)


def ensure_append_only(engine) -> list[str]:
    """Create append-only triggers and the baseline index if missing."""
    created: list[str] = []
    dialect = engine.dialect.name
    with engine.begin() as connection:
        if dialect == "sqlite":
            for name, statement in SQLITE_TRIGGERS.items():
                connection.execute(text(statement))
                created.append(name)
            connection.execute(text(SQLITE_BASELINE_INDEX))
            created.append("one_active_baseline")
        elif dialect == "postgresql":
            connection.execute(text(POSTGRES_FUNCTION))
            connection.execute(text(POSTGRES_TRUNCATE_FUNCTION))
            for name, statement in POSTGRES_TRIGGERS.items():
                connection.execute(text(statement))
                created.append(name)
            try:
                connection.execute(text(BASELINE_INDEX))
            except IntegrityError as exc:
                raise RuntimeError(
                    "存在多行 active 基线，拒绝启动；请先人工确认保留哪一条") from exc
            created.append("one_active_baseline")
    return created

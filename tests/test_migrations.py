"""Alembic migrations must produce the same schema as the SQLAlchemy models."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from engine.db import Base, make_engine

ROOT = Path(__file__).resolve().parents[1]


def run_alembic(*args: str, url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "NETCFG_DATABASE_URL": url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


def test_upgrade_creates_schema_and_matches_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    result = run_alembic("upgrade", "head", url=url)
    assert result.returncode == 0, result.stderr

    engine = make_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables

    indexes = {row["name"] for row in inspect(engine).get_indexes("baselines")}
    assert "one_active_baseline" in indexes

    # The schema produced by migrations must not drift from the models.
    check = run_alembic("check", url=url)
    assert check.returncode == 0, check.stdout + check.stderr


def test_existing_create_all_database_can_be_stamped(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO devices (id, name, created_at) VALUES ('d1', 'r1', datetime('now'))"))

    stamped = run_alembic("stamp", "head", url=url)
    assert stamped.returncode == 0, stamped.stderr
    check = run_alembic("check", url=url)
    assert check.returncode == 0, check.stdout + check.stderr

    with engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM devices")).scalar() == 1


def test_upgrade_is_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path / 'twice.db'}"
    assert run_alembic("upgrade", "head", url=url).returncode == 0
    assert run_alembic("upgrade", "head", url=url).returncode == 0

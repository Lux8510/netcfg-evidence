"""An existing database must gain the append-only triggers at startup, not only on first create."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from engine.bootstrap import ensure_append_only
from engine.db import Base, init_db, make_engine


@pytest.fixture()
def engine(tmp_path):
    tenant = make_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    Base.metadata.create_all(tenant)
    return tenant


def test_ensure_append_only_recreates_missing_triggers(engine):
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER audit_events_no_update"))
        connection.execute(text("DROP TRIGGER config_versions_no_update"))

    created = ensure_append_only(engine)
    assert set(created) >= {"audit_events_no_update", "config_versions_no_update"}

    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO devices (id, name, created_at) VALUES ('d1', 'r1', datetime('now'))"))
        connection.execute(text(
            "INSERT INTO audit_events (id, device_id, entity_type, entity_id, action, actor, note, snapshot, created_at) "
            "VALUES ('e1', 'd1', 'change_request', 'c1', 'created', '何深', 'note', '{}', datetime('now'))"))
    with pytest.raises(DatabaseError):
        with engine.begin() as connection:
            connection.execute(text("UPDATE audit_events SET note='amended' WHERE id='e1'"))


def test_init_db_is_idempotent_and_keeps_triggers(engine):
    init_db(engine)
    init_db(engine)
    with engine.begin() as connection:
        triggers = {row[0] for row in connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='trigger'"))}
    assert {"audit_events_no_update", "audit_events_no_delete", "config_versions_no_update"} <= triggers


def test_trigger_blocks_audit_update_after_restart(engine):
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER audit_events_no_update"))
    ensure_append_only(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO devices (id, name, created_at) VALUES ('d1', 'r1', datetime('now'))"))
        connection.execute(text(
            "INSERT INTO audit_events (id, device_id, entity_type, entity_id, action, actor, note, snapshot, created_at) "
            "VALUES ('e1', 'd1', 'change_request', 'c1', 'created', '何深', 'note', '{}', datetime('now'))"))
    with pytest.raises(DatabaseError):
        with engine.begin() as connection:
            connection.execute(text("UPDATE audit_events SET note='amended' WHERE id='e1'"))

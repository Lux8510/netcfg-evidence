"""Idempotent schema guarantees that must hold for existing databases too.

`create_all` only creates missing tables; it does not run triggers on tables
that already exist. Call this at startup so an older database gets the same
append-only guarantees as a fresh one.
"""

from __future__ import annotations

from sqlalchemy import text

SQLITE_TRIGGERS = {
    "audit_events_no_update": (
        "CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
    ),
    "audit_events_no_delete": (
        "CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
    ),
    "config_versions_no_update": (
        "CREATE TRIGGER IF NOT EXISTS config_versions_no_update BEFORE UPDATE ON config_versions "
        "BEGIN SELECT RAISE(ABORT, 'config_versions is append-only'); END"
    ),
}

POSTGRES_FUNCTION = """
CREATE OR REPLACE FUNCTION prevent_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$$ LANGUAGE plpgsql
"""

POSTGRES_TRIGGERS = {
    "audit_events_no_update": (
        "DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events; "
        "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
    "audit_events_no_delete": (
        "DROP TRIGGER IF EXISTS audit_events_no_delete ON audit_events; "
        "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
    "config_versions_no_update": (
        "DROP TRIGGER IF EXISTS config_versions_no_update ON config_versions; "
        "CREATE TRIGGER config_versions_no_update BEFORE UPDATE ON config_versions "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
}


def ensure_append_only(engine) -> list[str]:
    """Create the append-only triggers if they are missing. Returns created names."""
    created: list[str] = []
    dialect = engine.dialect.name
    with engine.begin() as connection:
        if dialect == "sqlite":
            for name, statement in SQLITE_TRIGGERS.items():
                connection.execute(text(statement))
                created.append(name)
        elif dialect == "postgresql":
            connection.execute(text(POSTGRES_FUNCTION))
            for name, statement in POSTGRES_TRIGGERS.items():
                connection.execute(text(statement))
                created.append(name)
    return created

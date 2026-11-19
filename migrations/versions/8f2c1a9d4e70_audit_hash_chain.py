"""audit hash chain

Revision ID: 8f2c1a9d4e70
Revises: 53d0c718f90e
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "8f2c1a9d4e70"
down_revision = "53d0c718f90e"
branch_labels = None
depends_on = None

GENESIS = "0" * 64


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _digest(seq, prev, row):
    snapshot = row["snapshot"]
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    body = {"seq": seq, "prev_hash": prev,
            "event": {"id": row["id"], "device_id": row["device_id"],
                      "entity_type": row["entity_type"], "entity_id": row["entity_id"],
                      "action": row["action"], "actor": row["actor"], "note": row["note"],
                      "snapshot": snapshot, "created_at": _iso(row["created_at"])}}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def upgrade() -> None:
    op.create_table(
        "audit_chain_head",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("last_hash", sa.String(length=64), nullable=False),
    )
    op.create_table(
        "audit_chain_entries",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36),
                  sa.ForeignKey("audit_events.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False, unique=True),
    )
    op.create_index("ix_audit_chain_event", "audit_chain_entries", ["event_id"])

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, device_id, entity_type, entity_id, action, actor, note, snapshot, created_at "
        "FROM audit_events ORDER BY created_at, id")).mappings().all()
    prev = GENESIS
    for seq, row in enumerate(rows, start=1):
        digest = _digest(seq, prev, row)
        bind.execute(sa.text(
            "INSERT INTO audit_chain_entries (seq, event_id, prev_hash, entry_hash) "
            "VALUES (:seq, :event_id, :prev, :digest)"),
            {"seq": seq, "event_id": row["id"], "prev": prev, "digest": digest})
        prev = digest
    bind.execute(sa.text(
        "INSERT INTO audit_chain_head (id, last_seq, last_hash) VALUES (1, :seq, :hash)"),
        {"seq": len(rows), "hash": prev})


def downgrade() -> None:
    op.drop_index("ix_audit_chain_event", table_name="audit_chain_entries")
    op.drop_table("audit_chain_entries")
    op.drop_table("audit_chain_head")

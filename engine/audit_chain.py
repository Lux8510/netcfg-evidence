"""Hash-chained audit trail.

Each audit event gets a chain entry whose hash covers the event facts, its
sequence number and the previous entry's hash. Verification recomputes the
whole chain and reports the first broken link. The head row is updated under a
write lock so concurrent appends cannot fork the chain.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from engine.models import AuditChainEntry, AuditChainHead, AuditEvent

GENESIS = "0" * 64


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def entry_body(seq: int, prev_hash: str, event: AuditEvent) -> dict[str, Any]:
    return {
        "seq": seq,
        "prev_hash": prev_hash,
        "event": {
            "id": event.id,
            "device_id": event.device_id,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "action": event.action,
            "actor": event.actor,
            "note": event.note,
            "snapshot": event.snapshot,
            "created_at": _iso(event.created_at),
        },
    }


def entry_hash(seq: int, prev_hash: str, event: AuditEvent) -> str:
    encoded = json.dumps(entry_body(seq, prev_hash, event), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _head(db: Session) -> AuditChainHead:
    head = db.get(AuditChainHead, 1)
    if head is None:
        head = AuditChainHead(id=1, last_seq=0, last_hash=GENESIS)
        db.add(head)
        db.flush()
    # A no-op UPDATE takes a write lock on SQLite and serializes head readers.
    db.execute(update(AuditChainHead).where(AuditChainHead.id == 1)
               .values(last_seq=AuditChainHead.last_seq))
    db.refresh(head)
    return head


def append_audit(db: Session, *, device_id: str, entity_type: str, entity_id: str,
                 action: str, actor: str, note: str, snapshot: dict[str, Any],
                 idempotency_key: str | None = None) -> AuditEvent:
    """Insert an audit event and its chain entry atomically in the caller's transaction."""
    head = _head(db)
    event = AuditEvent(device_id=device_id, entity_type=entity_type, entity_id=entity_id,
                       action=action, actor=actor, note=note, snapshot=snapshot,
                       idempotency_key=idempotency_key)
    db.add(event)
    db.flush()
    seq = head.last_seq + 1
    digest = entry_hash(seq, head.last_hash, event)
    db.add(AuditChainEntry(seq=seq, event_id=event.id, prev_hash=head.last_hash,
                           entry_hash=digest))
    head.last_seq = seq
    head.last_hash = digest
    db.flush()
    return event


def verify_chain(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(AuditChainEntry, AuditEvent)
        .join(AuditEvent, AuditEvent.id == AuditChainEntry.event_id)
        .order_by(AuditChainEntry.seq)
    ).all()
    expected_seq = 1
    prev = GENESIS
    for chain, event in rows:
        if chain.seq != expected_seq:
            return {"ok": False, "checked": expected_seq - 1, "first_bad_seq": expected_seq,
                    "reason": "chain sequence gap"}
        if chain.prev_hash != prev:
            return {"ok": False, "checked": expected_seq - 1, "first_bad_seq": chain.seq,
                    "reason": "prev_hash does not match previous entry"}
        if chain.entry_hash != entry_hash(chain.seq, prev, event):
            return {"ok": False, "checked": expected_seq - 1, "first_bad_seq": chain.seq,
                    "reason": "entry hash does not match event contents"}
        prev = chain.entry_hash
        expected_seq += 1
    total_events = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
    head = db.get(AuditChainHead, 1)
    if total_events != len(rows):
        return {"ok": False, "checked": len(rows), "first_bad_seq": len(rows) + 1,
                "reason": "audit event missing from the chain"}
    if head is None or head.last_seq != len(rows) or head.last_hash != prev:
        return {"ok": False, "checked": len(rows), "first_bad_seq": len(rows) + 1,
                "reason": "chain head is not consistent with the entries"}
    return {"ok": True, "checked": len(rows), "first_bad_seq": None,
            "head": prev, "reason": "ok"}

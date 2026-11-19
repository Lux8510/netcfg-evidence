"""Time-boxed drift exemptions.

An exemption matches one drift item by (path, hash of the current value), so a
later change on the same path is not silently covered. Expiry is evaluated at
read time: nothing has to run in the background to re-activate a drift.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.models import AuditEvent, DriftExemption, DriftReport

from .change_requests import validate_actor
from .service import ServiceError, _device

MAX_DAYS = 365


def value_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def create_exemption(db: Session, device_id: str, report_id: str, path: str,
                     reason: str, actor: str, expires_at: datetime) -> DriftExemption:
    _device(db, device_id)
    actor = validate_actor(actor)
    report = db.get(DriftReport, report_id)
    if report is None or report.device_id != device_id:
        raise ServiceError("DRIFT_NOT_FOUND", "漂移报告不存在或不属于该设备", 404)

    item = next((row for row in report.items if row.get("path") == path), None)
    if item is None:
        raise ServiceError("DRIFT_PATH_NOT_FOUND", "该报告中不存在这个漂移项", 422)

    reason = reason.strip()
    if not 10 <= len(reason) <= 1000:
        raise ServiceError("INVALID_REASON", "豁免原因需要 10–1000 个字符", 422)

    expires_at = _aware(expires_at).astimezone(timezone.utc)
    now = _now()
    if expires_at <= now:
        raise ServiceError("INVALID_EXPIRY", "到期时间必须晚于当前时间", 422)
    if expires_at > now + timedelta(days=MAX_DAYS):
        raise ServiceError("INVALID_EXPIRY", f"到期时间不能超过 {MAX_DAYS} 天", 422)

    exemption = DriftExemption(device_id=device_id, path=path, value_hash=value_hash(item.get("new")),
                               reason=reason, created_by=actor, expires_at=expires_at)
    try:
        with db.begin_nested():
            db.add(exemption)
            db.flush()
    except IntegrityError:
        raise ServiceError("EXEMPTION_EXISTS", "该漂移项已有生效中的豁免", 409) from None

    _audit(db, exemption, "created", actor, reason)
    return exemption


def list_exemptions(db: Session, device_id: str, include_inactive: bool = False) -> list[DriftExemption]:
    _device(db, device_id)
    query = select(DriftExemption).where(DriftExemption.device_id == device_id)
    if not include_inactive:
        query = query.where(DriftExemption.revoked_at.is_(None))
    return list(db.scalars(query.order_by(DriftExemption.created_at.desc(), DriftExemption.id)))


def revoke_exemption(db: Session, exemption_id: str, actor: str, reason: str) -> DriftExemption:
    exemption = db.get(DriftExemption, exemption_id)
    if exemption is None:
        raise ServiceError("EXEMPTION_NOT_FOUND", "豁免记录不存在", 404)
    if exemption.revoked_at is not None:
        raise ServiceError("EXEMPTION_REVOKED", "该豁免已经撤销", 409)
    actor = validate_actor(actor)
    reason = reason.strip()
    if not reason:
        raise ServiceError("INVALID_REASON", "请填写撤销原因", 422)
    exemption.revoked_at = _now()
    exemption.revoked_by = actor
    exemption.revoke_reason = reason
    _audit(db, exemption, "revoked", actor, reason)
    return exemption


def _audit(db: Session, exemption: DriftExemption, action: str, actor: str, note: str) -> None:
    from engine.audit_chain import append_audit
    append_audit(db, device_id=exemption.device_id, entity_type="drift_exemption",
                 entity_id=exemption.id, action=action, actor=actor, note=note,
                 snapshot=payload(exemption))


def payload(exemption: DriftExemption) -> dict[str, Any]:
    now = _now()
    expires_at = _aware(exemption.expires_at)
    return {
        "id": exemption.id,
        "device_id": exemption.device_id,
        "path": exemption.path,
        "reason": exemption.reason,
        "created_by": exemption.created_by,
        "expires_at": expires_at.isoformat(),
        "revoked_at": exemption.revoked_at.isoformat() if exemption.revoked_at else None,
        "revoked_by": exemption.revoked_by,
        "revoke_reason": exemption.revoke_reason,
        "created_at": exemption.created_at.isoformat(),
        "active": exemption.revoked_at is None and expires_at > now,
        "expired": exemption.revoked_at is None and expires_at <= now,
    }


def decorate_report(db: Session, report: DriftReport) -> dict[str, Any]:
    """Attach the matching active exemption to each item and split the summary."""
    from .service import drift_payload

    data = drift_payload(report)
    now = _now()
    active = [row for row in db.scalars(
        select(DriftExemption).where(DriftExemption.device_id == report.device_id,
                                     DriftExemption.revoked_at.is_(None))
    ) if _aware(row.expires_at) > now]
    index = {(row.path, row.value_hash): row for row in active}

    exempted = 0
    for item in data["items"]:
        match = index.get((item["path"], value_hash(item.get("new"))))
        if match is not None:
            exempted += 1
            item["exemption"] = payload(match)
        else:
            item["exemption"] = None
    summary = dict(data["summary"])
    summary["exempted"] = exempted
    summary["actionable"] = summary["total"] - exempted
    data["summary"] = summary
    return data

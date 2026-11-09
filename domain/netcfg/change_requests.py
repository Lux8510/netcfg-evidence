"""Change requests with mandatory human approval. The system never touches a device.

State machine:
    draft -> submit -> pending_approval -> approve -> approved -> apply -> applied
    pending_approval -> reject -> rejected
    draft -> cancel -> cancelled

Every transition writes one append-only AuditEvent. Replaying the same
idempotency key returns the stored result instead of writing twice.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.models import AuditEvent, ChangeRequest, Device, Finding, uid

from .service import ServiceError, _device, finding_payload

ACTOR_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff ._-]{1,79}$")
FORBIDDEN_ACTORS = {"system", "agent", "ai", "bot", "model", "assistant", "admin?自动"}
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

TRANSITIONS: dict[str, tuple[str, str]] = {
    "submit": ("draft", "pending_approval"),
    "approve": ("pending_approval", "approved"),
    "reject": ("pending_approval", "rejected"),
    "apply": ("approved", "applied"),
    "cancel": ("draft", "cancelled"),
}
MAX_COMMANDS = 50
MAX_COMMAND_LENGTH = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_actor(actor: str) -> str:
    actor = (actor or "").strip()
    if not ACTOR_RE.match(actor):
        raise ServiceError("INVALID_ACTOR", "必须填写真实审批人（2–80 个字符）", 422)
    if actor.casefold() in FORBIDDEN_ACTORS:
        raise ServiceError("HUMAN_APPROVAL_REQUIRED", "审批人不能是系统或 Agent 标识", 422)
    return actor


def validate_commands(commands: list[str]) -> list[str]:
    if not commands:
        raise ServiceError("EMPTY_COMMANDS", "变更单至少需要一条建议命令", 422)
    if len(commands) > MAX_COMMANDS:
        raise ServiceError("TOO_MANY_COMMANDS", f"建议命令最多 {MAX_COMMANDS} 条", 422)
    cleaned: list[str] = []
    for command in commands:
        if not isinstance(command, str):
            raise ServiceError("INVALID_COMMAND", "建议命令必须是文本", 422)
        text = command.strip()
        if not text:
            raise ServiceError("INVALID_COMMAND", "建议命令不能为空", 422)
        if len(text) > MAX_COMMAND_LENGTH:
            raise ServiceError("INVALID_COMMAND", f"单条命令不得超过 {MAX_COMMAND_LENGTH} 字符", 422)
        if CONTROL_RE.search(text):
            raise ServiceError("INVALID_COMMAND", "建议命令包含控制字符", 422)
        cleaned.append(text)
    return cleaned


def _device_findings(db: Session, device_id: str, finding_ids: list[str]) -> list[Finding]:
    if not finding_ids:
        raise ServiceError("NO_FINDINGS", "变更单必须关联至少一条 Finding", 422)
    if len(set(finding_ids)) != len(finding_ids):
        raise ServiceError("DUPLICATE_FINDING", "Finding 引用重复", 422)
    rows = []
    for finding_id in finding_ids:
        row = db.get(Finding, finding_id)
        if row is None or row.device_id != device_id:
            raise ServiceError("FINDING_MISMATCH", "Finding 不存在或不属于该设备", 422)
        if row.status != "violated":
            raise ServiceError("FINDING_NOT_ACTIONABLE", "只有 violated 的 Finding 能生成变更单", 422)
        rows.append(row)
    return rows


def create_change_request(db: Session, device_id: str, *, title: str, rationale: str,
                          finding_ids: list[str], proposed_commands: list[str],
                          actor: str) -> ChangeRequest:
    _device(db, device_id)
    actor = validate_actor(actor)
    title = title.strip()
    rationale = rationale.strip()
    if not 4 <= len(title) <= 160:
        raise ServiceError("INVALID_TITLE", "标题需要 4–160 个字符", 422)
    if not 10 <= len(rationale) <= 2000:
        raise ServiceError("INVALID_RATIONALE", "变更理由需要 10–2000 个字符", 422)
    findings = _device_findings(db, device_id, finding_ids)
    commands = validate_commands(proposed_commands)

    request = ChangeRequest(
        device_id=device_id, title=title, rationale=rationale, status="draft",
        finding_ids=[row.id for row in findings],
        findings_snapshot=[finding_payload(row) for row in findings],
        proposed_commands=commands, created_by=actor,
    )
    db.add(request)
    db.flush()
    _audit(db, request, "created", actor, rationale)
    return request


def list_change_requests(db: Session, device_id: str) -> list[ChangeRequest]:
    _device(db, device_id)
    return list(db.scalars(
        select(ChangeRequest).where(ChangeRequest.device_id == device_id)
        .order_by(ChangeRequest.created_at.desc(), ChangeRequest.id)
    ))


def get_change_request(db: Session, request_id: str) -> ChangeRequest:
    request = db.get(ChangeRequest, request_id)
    if request is None:
        raise ServiceError("CHANGE_REQUEST_NOT_FOUND", "变更单不存在", 404)
    return request


def transition(db: Session, request_id: str, action: str, *, actor: str, note: str,
               idempotency_key: str | None = None) -> ChangeRequest:
    if action not in TRANSITIONS:
        raise ServiceError("UNKNOWN_ACTION", "不支持的变更单操作", 422)
    actor = validate_actor(actor)
    note = (note or "").strip()
    if not note or len(note) > 1000:
        raise ServiceError("INVALID_NOTE", "请填写 1–1000 字符的操作说明", 422)

    if idempotency_key:
        replay = db.scalar(select(AuditEvent).where(AuditEvent.idempotency_key == idempotency_key))
        if replay is not None:
            if replay.entity_id != request_id or replay.action != action:
                raise ServiceError("IDEMPOTENCY_CONFLICT", "该幂等键已绑定其他操作", 409)
            return get_change_request(db, request_id)

    request = get_change_request(db, request_id)
    source, target = TRANSITIONS[action]
    if request.status != source:
        raise ServiceError("INVALID_TRANSITION",
                           f"当前状态 {request.status} 不能执行 {action}（需要 {source}）", 409)

    request.status = target
    request.updated_at = _now()
    if action == "approve":
        request.approved_by = actor
        request.approved_at = _now()
    if action == "reject":
        request.rejection_reason = note
    if action == "apply":
        request.applied_at = _now()

    _audit(db, request, action, actor, note, idempotency_key)
    return request


def _audit(db: Session, request: ChangeRequest, action: str, actor: str, note: str,
           idempotency_key: str | None = None) -> AuditEvent:
    event = AuditEvent(
        device_id=request.device_id, entity_type="change_request", entity_id=request.id,
        action=action, actor=actor, note=note, snapshot=request_payload(request),
        idempotency_key=idempotency_key,
    )
    db.add(event)
    db.flush()
    return event


def events(db: Session, request_id: str) -> list[AuditEvent]:
    get_change_request(db, request_id)
    return list(db.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "change_request",
                                 AuditEvent.entity_id == request_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ))


def request_payload(request: ChangeRequest) -> dict[str, Any]:
    return {
        "id": request.id,
        "device_id": request.device_id,
        "title": request.title,
        "rationale": request.rationale,
        "status": request.status,
        "finding_ids": request.finding_ids,
        "findings_snapshot": request.findings_snapshot,
        "proposed_commands": request.proposed_commands,
        "created_by": request.created_by,
        "approved_by": request.approved_by,
        "approved_at": request.approved_at.isoformat() if request.approved_at else None,
        "rejection_reason": request.rejection_reason,
        "applied_at": request.applied_at.isoformat() if request.applied_at else None,
        "created_at": request.created_at.isoformat(),
        "updated_at": request.updated_at.isoformat(),
        "executed_on_device": False,
    }


def event_payload(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "device_id": event.device_id,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "action": event.action,
        "actor": event.actor,
        "note": event.note,
        "snapshot": event.snapshot,
        "created_at": event.created_at.isoformat(),
    }

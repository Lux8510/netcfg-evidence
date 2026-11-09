"""Immutable configuration versions, one active baseline, stored drift reports."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, CheckConstraint, DDL, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, event, text)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class ConfigVersion(Base):
    __tablename__ = "config_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    parsed: Mapped[dict] = mapped_column(JSON, nullable=False)
    unparsed: Mapped[list] = mapped_column(JSON, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    __table_args__ = (UniqueConstraint("device_id", "raw_sha256", name="uq_config_device_raw"),)


class Baseline(Base):
    __tablename__ = "baselines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    config_version_id: Mapped[str] = mapped_column(ForeignKey("config_versions.id", ondelete="CASCADE"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    __table_args__ = (
        Index("one_active_baseline", "device_id", unique=True,
              sqlite_where=text("active = 1"), postgresql_where=text("active")),
    )


class DriftReport(Base):
    __tablename__ = "drift_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    baseline_id: Mapped[str] = mapped_column(ForeignKey("baselines.id", ondelete="CASCADE"), nullable=False)
    baseline_version_id: Mapped[str] = mapped_column(ForeignKey("config_versions.id", ondelete="CASCADE"), nullable=False)
    current_version_id: Mapped[str] = mapped_column(ForeignKey("config_versions.id", ondelete="CASCADE"), nullable=False)
    identical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    items: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    config_version_id: Mapped[str] = mapped_column(ForeignKey("config_versions.id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    __table_args__ = (CheckConstraint("status IN ('violated','undetermined')", name="ck_findings_status"),)


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    finding_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    findings_snapshot: Mapped[list] = mapped_column(JSON, nullable=False)
    proposed_commands: Mapped[list] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(80))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('draft','pending_approval','approved','rejected','applied','cancelled')",
                        name="ck_change_requests_status"),
    )


class AuditEvent(Base):
    """Append-only record of every state transition. No update or delete API."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id", "created_at"),)


class Explanation(Base):
    """Model output is stored with its validation result; rejected text is kept for audit."""

    __tablename__ = "explanations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    citations: Mapped[list] = mapped_column(JSON, nullable=False)
    usage: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('accepted','rejected')", name="ck_explanations_status"),
        UniqueConstraint("finding_id", "prompt_version", "model", name="uq_explanation_once"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    __table_args__ = (CheckConstraint("role IN ('editor','approver','admin')", name="ck_users_role"),)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie value
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DriftExemption(Base):
    """A reviewed, time-boxed exception for one drift item (path + current value)."""

    __tablename__ = "drift_exemptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(200), nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(80))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    __table_args__ = (
        Index("one_active_exemption", "device_id", "path", "value_hash", unique=True,
              sqlite_where=text("revoked_at IS NULL"), postgresql_where=text("revoked_at IS NULL")),
    )


class DeviceLink(Base):
    """One physical/logical link between two interfaces. Used by cross-device rules."""

    __tablename__ = "device_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_a_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    iface_a: Mapped[str] = mapped_column(String(80), nullable=False)
    device_b_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    iface_b: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    __table_args__ = (
        UniqueConstraint("device_a_id", "iface_a", name="uq_link_a"),
        UniqueConstraint("device_b_id", "iface_b", name="uq_link_b"),
        CheckConstraint("device_a_id <> device_b_id", name="ck_link_two_devices"),
    )


class RuleRun(Base):
    """One rule-check execution, including runs with zero findings."""

    __tablename__ = "rule_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    config_version_id: Mapped[str] = mapped_column(ForeignKey("config_versions.id", ondelete="CASCADE"), nullable=False)
    counts: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


# --- database-level guards, shared with startup bootstrap ---
def _register_guards() -> None:
    from sqlalchemy import DDL, event
    from .guards import POSTGRES_TRIGGERS, SQLITE_TRIGGERS

    for name, statement in SQLITE_TRIGGERS.items():
        table = next(t for t in (AuditEvent.__table__, ConfigVersion.__table__, Finding.__table__,
                                 Explanation.__table__, ChangeRequest.__table__)
                     if name.startswith(t.name))
        event.listen(table, "after_create", DDL(statement).execute_if(dialect="sqlite"))

    for name, statement in POSTGRES_TRIGGERS.items():
        table = next(t for t in (AuditEvent.__table__, ConfigVersion.__table__, Finding.__table__,
                                 Explanation.__table__, ChangeRequest.__table__)
                     if name.startswith(t.name))
        event.listen(table, "after_create", DDL(statement).execute_if(dialect="postgresql"))


_register_guards()

"""Application services. Every write goes through here so tests and API share one path."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.models import (AuditEvent, Baseline, ConfigVersion, Device, DeviceLink, DriftReport,
                           Finding, RuleRun)
from engine.models import uid

from .drift import diff_configs
from .parser import PARSER_VERSION, ParsedConfig, parse_frr_config


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _nested(db: Session):
    """Start a nested savepoint with a real outer transaction on SQLite.

    SQLite's default deferred transaction does not emit BEGIN, so a savepoint
    becomes the outermost transaction and RELEASE would commit its writes before
    a later rollback can undo them. Emit a real BEGIN first so the savepoint
    nests inside a transaction that a rollback can actually revert.
    """
    connection = db.connection()
    dbapi = getattr(connection, "connection", None)
    if connection.dialect.name == "sqlite" and dbapi is not None and not dbapi.in_transaction:
        connection.exec_driver_sql("BEGIN")
    return db.begin_nested()


def _device(db: Session, device_id: str) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise ServiceError("DEVICE_NOT_FOUND", "设备不存在", 404)
    return device


def create_device(db: Session, name: str) -> Device:
    name = name.strip()
    if not name:
        raise ServiceError("INVALID_NAME", "设备名不能为空", 422)
    if len(name) > 80:
        raise ServiceError("INVALID_NAME", "设备名过长", 422)
    if db.scalar(select(Device).where(Device.name == name)):
        raise ServiceError("DEVICE_EXISTS", "同名设备已存在", 409)
    device = Device(name=name)
    db.add(device)
    db.flush()
    return device


def get_device_or_404(db: Session, device_id: str) -> Device:
    return _device(db, device_id)


def list_devices(db: Session) -> list[Device]:
    return list(db.scalars(select(Device).order_by(Device.created_at, Device.id)))


def import_config(db: Session, device_id: str, text: str, source_name: str,
                  *, historical: bool = False) -> tuple[ConfigVersion, bool]:
    """Store a config version, preferring a current match for historical evidence.

    A regular import can create a current version even when identical evidence
    is already stored as a historical version.
    """
    device = _device(db, device_id)
    try:
        parsed: ParsedConfig = parse_frr_config(text, source_name or "config")
    except ValueError as exc:  # ConfigParseError
        raise ServiceError("CONFIG_UNUSABLE", str(exc), 422) from exc

    matches = select(ConfigVersion).where(
        ConfigVersion.device_id == device.id,
        ConfigVersion.raw_sha256 == parsed.raw_sha256,
    )
    if not historical:
        matches = matches.where(ConfigVersion.historical.is_(False))
    existing = db.scalar(matches.order_by(ConfigVersion.historical.asc()).limit(1))
    if existing is not None:
        return existing, False

    return _persist_version(db, device, parsed, historical=historical)


def _persist_version(db: Session, device: Device, parsed: ParsedConfig,
                     *, historical: bool = False) -> tuple[ConfigVersion, bool]:
    """Insert a version, resolving the unique-constraint race without a 500."""
    version = ConfigVersion(
        device_id=device.id,
        source_name=parsed.source_name,
        raw_text=parsed.raw_text,
        raw_sha256=parsed.raw_sha256,
        canonical_sha256=parsed.canonical_sha256,
        parser_version=PARSER_VERSION,
        parsed=parsed.canonical(),
        unparsed=[line.as_dict() for line in parsed.unparsed],
        line_count=parsed.line_count,
        historical=historical,
    )
    try:
        with _nested(db):
            db.add(version)
            db.flush()
    except IntegrityError:
        # A concurrent import won the unique constraint. Read the winner back.
        existing = db.scalar(
            select(ConfigVersion).where(
                ConfigVersion.device_id == device.id,
                ConfigVersion.raw_sha256 == parsed.raw_sha256,
                ConfigVersion.historical.is_(historical),
            )
        )
        if existing is None:
            raise
        return existing, False
    return version, True


def list_versions(db: Session, device_id: str, *, include_historical: bool = False) -> list[ConfigVersion]:
    _device(db, device_id)
    query = select(ConfigVersion).where(ConfigVersion.device_id == device_id)
    if not include_historical:
        query = query.where(ConfigVersion.historical.is_(False))
    return list(db.scalars(query.order_by(ConfigVersion.created_at.desc(), ConfigVersion.id.desc())))


def get_version(db: Session, version_id: str) -> ConfigVersion:
    version = db.get(ConfigVersion, version_id)
    if version is None:
        raise ServiceError("VERSION_NOT_FOUND", "配置版本不存在", 404)
    return version


def latest_version(db: Session, device_id: str) -> ConfigVersion:
    version = db.scalar(
        select(ConfigVersion)
        .where(ConfigVersion.device_id == device_id, ConfigVersion.historical.is_(False))
        .order_by(ConfigVersion.created_at.desc(), ConfigVersion.id.desc())
    )
    if version is None:
        raise ServiceError("NO_CONFIG", "该设备还没有配置版本", 409)
    return version


def active_baseline(db: Session, device_id: str) -> Baseline | None:
    return db.scalar(select(Baseline).where(Baseline.device_id == device_id, Baseline.active.is_(True)))


def set_baseline(db: Session, device_id: str, version_id: str) -> Baseline:
    _device(db, device_id)
    version = get_version(db, version_id)
    if version.device_id != device_id:
        raise ServiceError("VERSION_MISMATCH", "配置版本不属于该设备", 422)
    current = active_baseline(db, device_id)
    if current is not None:
        if current.config_version_id == version.id:
            return current
        current.active = False
        db.flush()
    return _activate_baseline(db, device_id, version)


def _activate_baseline(db: Session, device_id: str, version: ConfigVersion) -> Baseline:
    """Insert the active baseline, resolving the partial-unique race without a 500."""
    baseline = Baseline(device_id=device_id, config_version_id=version.id, active=True)
    try:
        with _nested(db):
            db.add(baseline)
            db.flush()
    except IntegrityError:
        # Another request activated a baseline first; report it instead of a 500.
        db.expire_all()
        winner = active_baseline(db, device_id)
        if winner is not None and winner.config_version_id == version.id:
            return winner
        current_id = winner.config_version_id if winner else None
        raise ServiceError("BASELINE_CONFLICT", f"基线已被并发更新，请刷新后重试（当前基线 {current_id}）", 409) from None
    return baseline


def compute_drift(db: Session, device_id: str, current_version_id: str | None = None) -> DriftReport:
    _device(db, device_id)
    baseline = active_baseline(db, device_id)
    if baseline is None:
        raise ServiceError("NO_BASELINE", "请先为该设备设置基线版本", 409)
    base_version = get_version(db, baseline.config_version_id)
    current = get_version(db, current_version_id) if current_version_id else latest_version(db, device_id)
    if current.device_id != device_id:
        raise ServiceError("VERSION_MISMATCH", "配置版本不属于该设备", 422)

    parsed_base = parse_frr_config(base_version.raw_text, base_version.source_name)
    parsed_current = parse_frr_config(current.raw_text, current.source_name)
    report = diff_configs(parsed_base, parsed_current)
    summary = report.summary()
    summary["unparsed_baseline"] = len(parsed_base.unparsed)
    summary["unparsed_current"] = len(parsed_current.unparsed)
    stored = DriftReport(
        device_id=device_id,
        baseline_id=baseline.id,
        baseline_version_id=base_version.id,
        current_version_id=current.id,
        identical=report.identical,
        summary=summary,
        items=[item.as_dict() for item in report.items],
    )
    db.add(stored)
    db.flush()
    return stored


def list_drifts(db: Session, device_id: str) -> list[DriftReport]:
    _device(db, device_id)
    return list(db.scalars(
        select(DriftReport)
        .where(DriftReport.device_id == device_id)
        .order_by(DriftReport.created_at.desc(), DriftReport.id.desc())
    ))


def version_payload(version: ConfigVersion, include_text: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": version.id,
        "device_id": version.device_id,
        "source_name": version.source_name,
        "raw_sha256": version.raw_sha256,
        "canonical_sha256": version.canonical_sha256,
        "line_count": version.line_count,
        "parser_version": parsed_version_of(version),
        "historical": bool(getattr(version, "historical", False)),
        "parsed": version.parsed,
        "unparsed": version.unparsed,
        "created_at": version.created_at.isoformat(),
    }
    if include_text:
        payload["text"] = version.raw_text
    return payload


def drift_payload(report: DriftReport) -> dict[str, Any]:
    return {
        "id": report.id,
        "device_id": report.device_id,
        "baseline_id": report.baseline_id,
        "baseline_version_id": report.baseline_version_id,
        "current_version_id": report.current_version_id,
        "identical": report.identical,
        "summary": report.summary,
        "items": report.items,
        "created_at": report.created_at.isoformat(),
    }


def run_rules_check(db: Session, device_id: str, version_id: str | None = None,
                    include_cross: bool = False,
                    peer_versions: dict[str, str] | None = None) -> dict[str, Any]:
    """Run the deterministic rule set against one version and persist the findings."""
    from . import cross_rules
    from . import rules as rules_module

    _device(db, device_id)
    version = get_version(db, version_id) if version_id else latest_version(db, device_id)
    if version.device_id != device_id:
        raise ServiceError("VERSION_MISMATCH", "配置版本不属于该设备", 422)

    parsed = parse_frr_config(version.raw_text, version.source_name)
    result = rules_module.run_rules(parsed)
    findings = list(result["findings"])
    cross_checked = 0
    cross_not_applicable = 0

    if include_cross:
        cross_checked = len(cross_rules.CROSS_RULES)
        links = active_links(db, device_id)
        if not links:
            cross_not_applicable = cross_checked
        else:
            peers = []
            for link in links:
                if link.device_a_id == device_id:
                    local_iface, peer_id, peer_iface = link.iface_a, link.device_b_id, link.iface_b
                else:
                    local_iface, peer_id, peer_iface = link.iface_b, link.device_a_id, link.iface_a
                peer_device = db.get(Device, peer_id)
                pinned = (peer_versions or {}).get(peer_id)
                if pinned is not None:
                    peer_version = db.get(ConfigVersion, pinned)
                    if peer_version is None or peer_version.device_id != peer_id:
                        raise ServiceError("PEER_VERSION_MISMATCH", "指定的对端版本不属于该对端设备", 422)
                else:
                    peer_version = db.scalar(
                        select(ConfigVersion).where(ConfigVersion.device_id == peer_id,
                                                    ConfigVersion.historical.is_(False))
                        .order_by(ConfigVersion.created_at.desc(), ConfigVersion.id.desc()).limit(1))
                if peer_version is None:
                    findings.append(rules_module.Finding(
                        cross_rules.LINK_MISMATCH, "undetermined",
                        f"对端设备 {peer_device.name if peer_device else peer_id} 还没有配置版本，无法比较链路。",
                        (rules_module.Evidence("parsed_value", None, "peer config missing",
                                               f"interface.{local_iface}", version.id),)))
                    continue
                peers.append(cross_rules.Peer(
                    device_id=peer_id, device_name=peer_device.name if peer_device else peer_id,
                    version_id=peer_version.id, interface=peer_iface,
                    parsed=parse_frr_config(peer_version.raw_text, peer_version.source_name)))
                findings.extend(cross_rules.check_link(parsed, version.id, local_iface, peers[-1]))
            findings.extend(cross_rules.check_duplicate_addresses(parsed, version.id, peers))

    digest_matches = parsed.canonical_sha256 == version.canonical_sha256
    run_id = uid()
    covered = {finding.rule_id for finding in findings}
    counts = {
        "rules_checked": result["rules_checked"] + cross_checked,
        "rules_satisfied": (result["rules_checked"] + cross_checked - len(covered)
                            - result.get("rules_not_applicable", 0) - cross_not_applicable),
        "rules_not_applicable": result.get("rules_not_applicable", 0) + cross_not_applicable,
        "findings_violated": sum(1 for finding in findings if finding.status == "violated"),
        "findings_undetermined": sum(1 for finding in findings if finding.status == "undetermined"),
    }
    db.add(RuleRun(id=run_id, device_id=device_id, config_version_id=version.id, counts=counts))
    stored = []
    for finding in findings:
        row = Finding(
            run_id=run_id, device_id=device_id, config_version_id=version.id,
            rule_id=finding.rule_id, status=finding.status, message=finding.message,
            evidence=[item.as_dict() for item in finding.evidence],
        )
        db.add(row)
        stored.append(row)
    db.flush()
    return {
        "run_id": run_id,
        "device_id": device_id,
        "config_version_id": version.id,
        "digest": {"sha256": version.raw_sha256, "canonical_sha256": version.canonical_sha256,
                   "recomputed_canonical_sha256": parsed.canonical_sha256,
                   "parser_version": parsed_version_of(version),
        "historical": bool(getattr(version, "historical", False)), "matches": digest_matches},
        "counts": counts,
        "findings": [finding_payload(row) for row in stored],
    }


def active_links(db: Session, device_id: str) -> list[DeviceLink]:
    return list(db.scalars(
        select(DeviceLink).where((DeviceLink.device_a_id == device_id) | (DeviceLink.device_b_id == device_id))
        .order_by(DeviceLink.created_at, DeviceLink.id)))


def create_link(db: Session, device_a_id: str, iface_a: str, device_b_id: str, iface_b: str) -> DeviceLink:
    if device_a_id == device_b_id:
        raise ServiceError("LINK_SAME_DEVICE", "链路两端不能是同一台设备", 422)
    for device_id, iface in ((device_a_id, iface_a), (device_b_id, iface_b)):
        _device(db, device_id)
        if not iface.strip():
            raise ServiceError("LINK_INVALID_INTERFACE", "接口名不能为空", 422)
    existing = db.scalar(select(DeviceLink).where(DeviceLink.device_a_id == device_a_id, DeviceLink.iface_a == iface_a))
    if existing:
        raise ServiceError("LINK_EXISTS", "该接口已经登记链路", 409)
    existing = db.scalar(select(DeviceLink).where(DeviceLink.device_b_id == device_b_id, DeviceLink.iface_b == iface_b))
    if existing:
        raise ServiceError("LINK_EXISTS", "对端接口已经登记链路", 409)
    link = DeviceLink(device_a_id=device_a_id, iface_a=iface_a.strip(),
                      device_b_id=device_b_id, iface_b=iface_b.strip())
    db.add(link)
    db.flush()
    return link


def list_links(db: Session, device_id: str | None = None) -> list[DeviceLink]:
    query = select(DeviceLink).order_by(DeviceLink.created_at, DeviceLink.id)
    if device_id:
        _device(db, device_id)
        query = select(DeviceLink).where(
            (DeviceLink.device_a_id == device_id) | (DeviceLink.device_b_id == device_id)
        ).order_by(DeviceLink.created_at, DeviceLink.id)
    return list(db.scalars(query))


def delete_link(db: Session, link_id: str) -> None:
    link = db.get(DeviceLink, link_id)
    if link is None:
        raise ServiceError("LINK_NOT_FOUND", "链路不存在", 404)
    db.delete(link)


def link_payload(db: Session, link: DeviceLink) -> dict[str, Any]:
    def name(device_id: str) -> str:
        device = db.get(Device, device_id)
        return device.name if device else device_id
    return {"id": link.id,
            "device_a": {"id": link.device_a_id, "name": name(link.device_a_id), "interface": link.iface_a},
            "device_b": {"id": link.device_b_id, "name": name(link.device_b_id), "interface": link.iface_b},
            "created_at": link.created_at.isoformat()}


def parsed_version_of(version: ConfigVersion) -> str:
    return getattr(version, "parser_version", None) or "unknown"


def list_audit(db: Session, device_id: str, limit: int = 100) -> list[AuditEvent]:
    _device(db, device_id)
    return list(db.scalars(
        select(AuditEvent).where(AuditEvent.device_id == device_id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)
    ))


def audit_payload(row: AuditEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "device_id": row.device_id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "action": row.action,
        "actor": row.actor,
        "note": row.note,
        "created_at": row.created_at.isoformat(),
    }


def list_rule_runs(db: Session, device_id: str, limit: int = 20) -> list[RuleRun]:
    _device(db, device_id)
    return list(db.scalars(
        select(RuleRun).where(RuleRun.device_id == device_id)
        .order_by(RuleRun.created_at.desc(), RuleRun.id.desc()).limit(limit)
    ))


def rule_run_payload(row: RuleRun) -> dict[str, Any]:
    return {
        "run_id": row.id,
        "device_id": row.device_id,
        "config_version_id": row.config_version_id,
        "counts": row.counts,
        "created_at": row.created_at.isoformat(),
    }


def finding_payload(row: Finding) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "device_id": row.device_id,
        "config_version_id": row.config_version_id,
        "rule_id": row.rule_id,
        "status": row.status,
        "message": row.message,
        "evidence": row.evidence,
        "created_at": row.created_at.isoformat(),
    }


def list_findings(db: Session, device_id: str, run_id: str | None = None) -> list[Finding]:
    _device(db, device_id)
    query = select(Finding).where(Finding.device_id == device_id)
    if run_id:
        query = query.where(Finding.run_id == run_id)
    return list(db.scalars(query.order_by(Finding.created_at.desc(), Finding.id)))


def version_lines(db: Session, version_id: str, start: int, end: int) -> dict[str, Any]:
    version = get_version(db, version_id)
    if start < 1 or end < start or (end - start + 1) > 200:
        raise ServiceError("LINE_RANGE_INVALID", "行号范围无效或超过 200 行", 422)
    if start > version.line_count:
        raise ServiceError("LINE_RANGE_INVALID", "行号范围超出该版本行数", 422)
    end = min(end, version.line_count)
    lines = version.raw_text.splitlines()
    return {
        "version_id": version.id,
        "start": start,
        "end": end,
        "lines": [{"line": number, "text": lines[number - 1]} for number in range(start, end + 1)],
    }

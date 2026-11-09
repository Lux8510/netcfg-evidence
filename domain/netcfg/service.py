"""Application services. Every write goes through here so tests and API share one path."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.models import Baseline, ConfigVersion, Device, DriftReport, Finding
from engine.models import uid

from .drift import diff_configs
from .parser import PARSER_VERSION, ParsedConfig, parse_frr_config


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


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


def import_config(db: Session, device_id: str, text: str, source_name: str) -> tuple[ConfigVersion, bool]:
    """Parse and store a version. Re-importing identical bytes returns the existing row."""
    device = _device(db, device_id)
    try:
        parsed: ParsedConfig = parse_frr_config(text, source_name or "config")
    except ValueError as exc:  # ConfigParseError
        raise ServiceError("CONFIG_UNUSABLE", str(exc), 422) from exc

    existing = db.scalar(
        select(ConfigVersion).where(
            ConfigVersion.device_id == device.id,
            ConfigVersion.raw_sha256 == parsed.raw_sha256,
        )
    )
    if existing is not None:
        return existing, False

    return _persist_version(db, device, parsed)


def _persist_version(db: Session, device: Device, parsed: ParsedConfig) -> tuple[ConfigVersion, bool]:
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
    )
    try:
        with db.begin_nested():
            db.add(version)
            db.flush()
    except IntegrityError:
        # A concurrent import won the unique constraint. Read the winner back.
        existing = db.scalar(
            select(ConfigVersion).where(
                ConfigVersion.device_id == device.id,
                ConfigVersion.raw_sha256 == parsed.raw_sha256,
            )
        )
        if existing is None:
            raise
        return existing, False
    return version, True


def list_versions(db: Session, device_id: str) -> list[ConfigVersion]:
    _device(db, device_id)
    return list(db.scalars(
        select(ConfigVersion)
        .where(ConfigVersion.device_id == device_id)
        .order_by(ConfigVersion.created_at.desc(), ConfigVersion.id.desc())
    ))


def get_version(db: Session, version_id: str) -> ConfigVersion:
    version = db.get(ConfigVersion, version_id)
    if version is None:
        raise ServiceError("VERSION_NOT_FOUND", "配置版本不存在", 404)
    return version


def latest_version(db: Session, device_id: str) -> ConfigVersion:
    version = db.scalar(
        select(ConfigVersion)
        .where(ConfigVersion.device_id == device_id)
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
        with db.begin_nested():
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
        "current_version_id": report.current_version_id,
        "identical": report.identical,
        "summary": report.summary,
        "items": report.items,
        "created_at": report.created_at.isoformat(),
    }


def run_rules_check(db: Session, device_id: str, version_id: str | None = None) -> dict[str, Any]:
    """Run the deterministic rule set against one version and persist the findings."""
    from . import rules as rules_module

    _device(db, device_id)
    version = get_version(db, version_id) if version_id else latest_version(db, device_id)
    if version.device_id != device_id:
        raise ServiceError("VERSION_MISMATCH", "配置版本不属于该设备", 422)

    parsed = parse_frr_config(version.raw_text, version.source_name)
    result = rules_module.run_rules(parsed)
    digest_matches = parsed.canonical_sha256 == version.canonical_sha256
    run_id = uid()
    stored = []
    for finding in result["findings"]:
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
                   "parser_version": parsed_version_of(version), "matches": digest_matches},
        "counts": {
            "rules_checked": result["rules_checked"],
            "rules_satisfied": result["rules_satisfied"],
            "rules_not_applicable": result.get("rules_not_applicable", 0),
            "findings_violated": result["findings_violated"],
            "findings_undetermined": result["findings_undetermined"],
        },
        "findings": [finding_payload(row) for row in stored],
    }


def parsed_version_of(version: ConfigVersion) -> str:
    return getattr(version, "parser_version", None) or "unknown"


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

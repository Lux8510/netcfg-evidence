"""Application services. Every write goes through here so tests and API share one path."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.models import Baseline, ConfigVersion, Device, DriftReport

from .drift import diff_configs
from .parser import ParsedConfig, parse_frr_config


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

    version = ConfigVersion(
        device_id=device.id,
        source_name=parsed.source_name,
        raw_text=parsed.raw_text,
        raw_sha256=parsed.raw_sha256,
        canonical_sha256=parsed.canonical_sha256,
        parsed=parsed.canonical(),
        unparsed=[line.as_dict() for line in parsed.unparsed],
        line_count=parsed.line_count,
    )
    db.add(version)
    db.flush()
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
    baseline = Baseline(device_id=device_id, config_version_id=version.id, active=True)
    db.add(baseline)
    db.flush()
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

    report = diff_configs(
        parse_frr_config(base_version.raw_text, base_version.source_name),
        parse_frr_config(current.raw_text, current.source_name),
    )
    stored = DriftReport(
        device_id=device_id,
        baseline_id=baseline.id,
        current_version_id=current.id,
        identical=report.identical,
        summary=report.summary(),
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

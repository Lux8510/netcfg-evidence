"""Device targets, read-only collection, and before/after change verification."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from collectors import ssh as ssh_collector
from engine.models import ChangeRun, CollectionEvent, ConfigVersion, Device, DeviceTarget

from .drift import diff_configs
from .parser import PARSER_VERSION, parse_frr_config
from .rules import run_rules
from .service import ServiceError, _device, get_version, latest_version

Collector = Callable[[ssh_collector.Target], dict]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def set_target(db: Session, device_id: str, *, host: str, port: int, username: str,
               credential_ref: str | None = None, key_path: str | None = None,
               enabled: bool = True) -> DeviceTarget:
    _device(db, device_id)
    host, username = host.strip(), username.strip()
    if not host:
        raise ServiceError("TARGET_INVALID", "主机地址不能为空", 422)
    allowed_hosts = {item.strip() for item in os.environ.get("NETCFG_SSH_ALLOWED_HOSTS", "").split(",") if item.strip()}
    if allowed_hosts and host not in allowed_hosts:
        raise ServiceError("TARGET_HOST_NOT_ALLOWED", "该主机不在服务端允许的采集范围内", 422)
    if credential_ref:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,60}", credential_ref):
            raise ServiceError("TARGET_INVALID", "凭据引用必须是环境变量名（大写字母、数字、下划线）", 422)
        allowed_refs = {item.strip() for item in os.environ.get("NETCFG_SSH_ALLOWED_CREDENTIAL_REFS", "").split(",") if item.strip()}
        if allowed_refs and credential_ref not in allowed_refs:
            raise ServiceError("TARGET_CREDENTIAL_NOT_ALLOWED", "该凭据引用未在服务端允许列表中", 422)
    if not 1 <= port <= 65535:
        raise ServiceError("TARGET_INVALID", "端口需在 1–65535", 422)
    if not username:
        raise ServiceError("TARGET_INVALID", "用户名不能为空", 422)
    target = db.get(DeviceTarget, device_id)
    if target is None:
        target = DeviceTarget(device_id=device_id, host=host, port=port, username=username,
                              credential_ref=credential_ref, key_path=key_path, enabled=enabled)
        db.add(target)
    else:
        target.host, target.port, target.username = host, port, username
        target.credential_ref, target.key_path, target.enabled = credential_ref, key_path, enabled
        target.updated_at = _now()
    db.flush()
    return target


def get_target(db: Session, device_id: str) -> DeviceTarget | None:
    _device(db, device_id)
    return db.get(DeviceTarget, device_id)


def target_payload(target: DeviceTarget) -> dict[str, Any]:
    return {
        "device_id": target.device_id,
        "host": target.host,
        "port": target.port,
        "username": target.username,
        "credential_ref": target.credential_ref,
        "key_path": target.key_path,
        "enabled": target.enabled,
        "updated_at": target.updated_at.isoformat(),
        "secret_stored": False,
    }


def _target_from_row(target: DeviceTarget) -> ssh_collector.Target:
    return ssh_collector.target_from_env(target.host, target.port, target.username,
                                         target.credential_ref, target.key_path)


def _store_version(db: Session, device_id: str, label: str, config_text: str) -> ConfigVersion:
    parsed = parse_frr_config(config_text, f"ssh:{label}")
    existing = db.scalar(select(ConfigVersion).where(
        ConfigVersion.device_id == device_id,
        ConfigVersion.raw_sha256 == parsed.raw_sha256))
    if existing is not None:
        return existing
    version = ConfigVersion(
        device_id=device_id, source_name=f"ssh:{label}", raw_text=parsed.raw_text,
        raw_sha256=parsed.raw_sha256, canonical_sha256=parsed.canonical_sha256,
        parser_version=PARSER_VERSION, parsed=parsed.canonical(),
        unparsed=[line.as_dict() for line in parsed.unparsed], line_count=parsed.line_count)
    db.add(version)
    db.flush()
    return version


def collect_device(db: Session, device_id: str, label: str,
                   collector: Collector | None = None) -> tuple[ConfigVersion, CollectionEvent]:
    target = db.get(DeviceTarget, device_id)
    if target is None:
        raise ServiceError("TARGET_NOT_CONFIGURED", "请先配置设备的 SSH 目标", 409)
    if not target.enabled:
        raise ServiceError("TARGET_DISABLED", "该设备的采集已禁用", 409)
    collector = collector or ssh_collector.collect
    started = _now()
    try:
        result = collector(_target_from_row(target))
    except ssh_collector.CollectionError as exc:
        db.add(CollectionEvent(device_id=device_id, label=label, target_host=target.host,
                               status="failed", error_code=exc.code, error_message=exc.message,
                               commands=[], started_at=started, finished_at=_now()))
        db.flush()
        # Commit the failed event before the API turns this into a 502/rollback.
        db.commit()
        raise ServiceError(exc.code, exc.message, 502) from exc
    version = _store_version(db, device_id, label, result["config_text"])
    event = CollectionEvent(
        device_id=device_id, label=label, target_host=target.host, status="success",
        commands=[{"command": item["command"], "exit_code": item["exit_code"],
                   "stdout": item["stdout"], "stderr": item["stderr"]} for item in result["commands"]],
        version_id=version.id, started_at=started, finished_at=_now())
    db.add(event)
    db.flush()
    return version, event


def collection_payload(event: CollectionEvent) -> dict[str, Any]:
    return {
        "id": event.id, "device_id": event.device_id, "label": event.label,
        "target_host": event.target_host, "status": event.status,
        "error_code": event.error_code, "error_message": event.error_message,
        "commands": [{"command": item["command"], "exit_code": item["exit_code"],
                      "bytes": len(item["stdout"])} for item in event.commands],
        "version_id": event.version_id,
        "started_at": event.started_at.isoformat(), "finished_at": event.finished_at.isoformat(),
    }


def start_change_run(db: Session, device_id: str, note: str, actor: str,
                     collector: Collector | None = None,
                     label: str = "before") -> ChangeRun:
    _device(db, device_id)
    note = note.strip()
    if not 10 <= len(note) <= 1000:
        raise ServiceError("INVALID_NOTE", "变更说明需要 10–1000 个字符", 422)
    target = db.get(DeviceTarget, device_id)
    version, _ = collect_device(db, device_id, f"{label}-{_now().strftime('%Y%m%dT%H%M%SZ')}", collector)
    run = ChangeRun(device_id=device_id, note=note, created_by=actor, status="open",
                    target_host=target.host, target_port=target.port, target_username=target.username,
                    before_version_id=version.id)
    db.add(run)
    db.flush()
    return run


def complete_change_run(db: Session, run_id: str, note: str,
                        collector: Collector | None = None) -> ChangeRun:
    from sqlalchemy import update

    run = db.get(ChangeRun, run_id)
    if run is None:
        raise ServiceError("CHANGE_RUN_NOT_FOUND", "变更核对不存在", 404)
    note = note.strip()
    if not note:
        raise ServiceError("INVALID_NOTE", "请填写核对说明", 422)

    # Compare-and-set: only one caller may turn open -> completing.
    claimed = db.execute(update(ChangeRun)
                         .where(ChangeRun.id == run_id, ChangeRun.status == "open")
                         .values(status="completing"))
    if claimed.rowcount != 1:
        db.expire_all()
        current = db.get(ChangeRun, run_id)
        if current is not None and current.status == "completed":
            return current
        raise ServiceError("CHANGE_RUN_CLOSED", "该变更核对已经结束或正在核对", 409)
    db.flush()

    target = db.get(DeviceTarget, run.device_id)
    if target is None or (target.host, target.port, target.username) != (
            run.target_host, run.target_port, run.target_username):
        run.status = "failed"
        run.error_code, run.error_message = "TARGET_CHANGED", "采集目标在变更窗口期间发生变化，拒绝核对"
        run.completed_at = _now()
        db.commit()
        raise ServiceError("TARGET_CHANGED", run.error_message, 409)

    try:
        version, _ = collect_device(db, run.device_id, "after", collector)
    except ServiceError as exc:
        run.status = "failed"
        run.error_code, run.error_message = exc.code, exc.message
        run.completed_at = _now()
        db.commit()
        raise
    before = get_version(db, run.before_version_id)
    report = diff_configs(parse_frr_config(before.raw_text, before.source_name),
                          parse_frr_config(version.raw_text, version.source_name))
    parsed_after = parse_frr_config(version.raw_text, version.source_name)
    rule_result = run_rules(parsed_after)
    run.after_version_id = version.id
    run.status = "completed"
    run.diff_items = [item.as_dict() for item in report.items]
    run.diff_summary = report.summary()
    run.findings = [finding.as_dict() for finding in rule_result["findings"]]
    run.findings_counts = {key: value for key, value in rule_result.items() if key != "findings"}
    run.completed_at = _now()
    db.flush()
    return run


def list_change_runs(db: Session, device_id: str) -> list[ChangeRun]:
    _device(db, device_id)
    return list(db.scalars(select(ChangeRun).where(ChangeRun.device_id == device_id)
                           .order_by(ChangeRun.created_at.desc(), ChangeRun.id)))


def get_change_run(db: Session, run_id: str) -> ChangeRun:
    run = db.get(ChangeRun, run_id)
    if run is None:
        raise ServiceError("CHANGE_RUN_NOT_FOUND", "变更核对不存在", 404)
    return run


def change_run_payload(db: Session, run: ChangeRun) -> dict[str, Any]:
    def version_payload(version_id: str | None):
        if not version_id:
            return None
        version = db.get(ConfigVersion, version_id)
        return {"id": version.id, "source_name": version.source_name,
                "canonical_sha256": version.canonical_sha256,
                "created_at": version.created_at.isoformat()} if version else None
    return {
        "id": run.id, "device_id": run.device_id, "note": run.note, "status": run.status,
        "created_by": run.created_by, "created_at": run.created_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "before": version_payload(run.before_version_id),
        "after": version_payload(run.after_version_id),
        "diff_summary": run.diff_summary, "diff_items": run.diff_items,
        "findings": run.findings, "findings_counts": run.findings_counts,
        "error_code": run.error_code, "error_message": run.error_message,
    }

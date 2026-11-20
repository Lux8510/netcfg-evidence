"""Import a NetSecOps diagnosis bundle into a device's evidence history.

The bundle is untrusted: it must be structurally valid, its two digests must
match, and its snapshot must be bound to a successful running-config observation
for the node it claims. Import is idempotent per (device, bundle) and per
(device, trace). It never moves a baseline, and it never uses the external
commands as draft content — a draft is created only for a relevant, locally
violated finding, with a remediation derived from that finding.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.models import DiagnosisImport, Finding

from . import change_requests
from .service import ServiceError, _device, import_config, run_rules_check


ALLOWED_STATUS = ("normal", "ospf_area_mismatch", "interface_admin_down", "ospf_passive",
                  "insufficient_evidence", "collection_failure")

# Diagnosis status -> netcfg rule ids that may justify a change request.
DIAGNOSIS_RULES: dict[str, tuple[str, ...]] = {
    "ospf_area_mismatch": ("R005",),  # cross-device link area consistency
}


class ImportError_(ValueError):
    pass


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _require(value: Any, condition: bool, message: str) -> None:
    if not condition:
        raise ImportError_(message)


def verify_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    _require(bundle, isinstance(bundle, dict), "bundle must be an object")
    _require(bundle, bundle.get("source") == "netsecops-agent", "bundle source is not netsecops-agent")
    _require(bundle, bundle.get("schema_version") == 2, "unsupported bundle schema version")
    _require(bundle, bundle.get("lab_only") is True, "bundle is not marked lab-only")

    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    _require(bundle, hashlib.sha256(canonical(body)).hexdigest() == bundle.get("bundle_sha256"),
             "bundle hash does not match its contents")

    trace_id = bundle.get("trace_id")
    _require(bundle, isinstance(trace_id, str) and len(trace_id) == 64,
             "bundle has no valid trace_id")

    device = bundle.get("device") or {}
    _require(bundle, isinstance(device.get("name"), str) and device["name"].strip(),
             "bundle has no device name")

    diagnosis = bundle.get("diagnosis") or {}
    _require(bundle, isinstance(diagnosis, dict), "diagnosis must be an object")
    _require(bundle, diagnosis.get("status") in ALLOWED_STATUS, "unknown diagnosis status")
    _require(bundle, isinstance(diagnosis.get("conclusion"), str), "diagnosis has no conclusion")
    _require(bundle, isinstance(diagnosis.get("evidence_ids"), list), "diagnosis evidence_ids is not a list")

    snapshot = bundle.get("config_snapshot") or {}
    _require(bundle, isinstance(snapshot, dict), "config_snapshot must be an object")
    _require(bundle, isinstance(snapshot.get("node"), str) and snapshot["node"].strip(),
             "snapshot has no source node")
    _require(bundle, isinstance(snapshot.get("target"), str) and snapshot["target"].strip(),
             "snapshot has no target")
    _require(bundle, snapshot["node"] == snapshot["target"], "snapshot node and target differ")
    _require(bundle, device["name"].strip() == snapshot["node"],
             "device name does not match snapshot node")
    _require(bundle, isinstance(snapshot.get("evidence_id"), str) and snapshot["evidence_id"],
             "snapshot has no evidence_id")
    command = snapshot.get("command")
    _require(bundle, isinstance(command, list) and command
             and all(isinstance(part, str) for part in command), "snapshot command is invalid")
    _require(bundle, any("show running-config" in part for part in command),
             "snapshot command is not a running-config collection")
    _require(bundle, snapshot.get("exit_code") == 0, "snapshot command did not succeed")
    text = snapshot.get("text")
    _require(bundle, isinstance(text, str) and text.strip(), "snapshot has no config text")
    _require(bundle, hashlib.sha256(text.encode("utf-8")).hexdigest() == snapshot.get("sha256"),
             "config snapshot hash does not match")

    _require(bundle, isinstance(bundle.get("proposed_commands"), list),
             "bundle has no proposed_commands list")
    _require(bundle, all(isinstance(item, str) for item in bundle["proposed_commands"]),
             "proposed_commands must be strings")

    citations = bundle.get("citations")
    _require(bundle, isinstance(citations, list) and citations, "bundle has no citations")
    by_id: dict[str, dict[str, Any]] = {}
    for item in citations:
        _require(bundle, isinstance(item, dict), "citation must be an object")
        _require(bundle, isinstance(item.get("evidence_id"), str) and item["evidence_id"],
                 "citation has no evidence_id")
        _require(bundle, isinstance(item.get("node"), str) or item.get("node") is None,
                 "citation node is invalid")
        _require(bundle, isinstance(item.get("content_sha256"), str) and len(item["content_sha256"]) == 64,
                 "citation has no content digest")
        by_id[item["evidence_id"]] = item

    snapshot_citation = by_id.get(snapshot["evidence_id"])
    _require(bundle, snapshot_citation is not None, "snapshot evidence is missing from citations")
    _require(bundle, snapshot_citation["node"] == snapshot["node"],
             "snapshot citation belongs to another node")
    _require(bundle, snapshot_citation["content_sha256"] == snapshot["sha256"],
             "snapshot citation content does not match the snapshot")
    return bundle


def _derive_remediation(finding: Finding) -> list[str]:
    """Build a remediation from the local finding, never from the external commands."""
    if finding.rule_id != "R005":
        return []
    evidence = finding.evidence or []
    if len(evidence) < 2:
        return []
    local, peer = evidence[0], evidence[1]
    parts = (local.get("path") or "").split(".")
    if len(parts) < 2 or parts[0] != "interface" or not parts[1]:
        return []
    iface = parts[1]
    peer_text = (peer.get("text") or "").strip()
    if not peer_text.startswith("area "):
        return []
    peer_area = peer_text[len("area "):].strip()
    if not peer_area:
        return []
    return [f"interface {iface}", f"ip ospf area {peer_area}"]


def import_diagnosis(db: Session, device_id: str, bundle: dict[str, Any], actor: str,
                     *, preferred_rule: str | None = None) -> dict[str, Any]:
    bundle = verify_bundle(bundle)
    _device(db, device_id)

    existing = db.scalar(select(DiagnosisImport).where(
        DiagnosisImport.device_id == device_id,
        (DiagnosisImport.bundle_sha256 == bundle["bundle_sha256"])
        | (DiagnosisImport.trace_id == bundle["trace_id"]),
    ))
    if existing is not None:
        return import_payload(existing, duplicate=True)

    snapshot = bundle["config_snapshot"]
    diagnosis = bundle["diagnosis"]
    external_commands = [item for item in bundle["proposed_commands"] if isinstance(item, str) and item.strip()]

    version, _created = import_config(db, device_id, snapshot["text"],
                                      f"netsecops-{bundle['bundle_sha256'][:8]}")
    run = run_rules_check(db, device_id, version.id, include_cross=True)
    if run["config_version_id"] != version.id:
        raise ServiceError("RULE_VERSION_MISMATCH", "规则运行未绑定导入的版本", 500)
    if any(item.get("config_version_id") != version.id for item in run["findings"]):
        raise ServiceError("FINDING_VERSION_MISMATCH", "Finding 未绑定导入的版本", 500)

    # Resolve eligibility against the stored ORM finding rows.
    rules = DIAGNOSIS_RULES.get(diagnosis["status"], ())
    if preferred_rule:
        if preferred_rule not in rules:
            rules = ()
            no_draft_reason = f"指定规则 {preferred_rule} 不适用于诊断 {diagnosis['status']}"
        else:
            rules = (preferred_rule,)
    eligible: Finding | None = None
    no_draft_reason = None
    if rules:
        for item in run["findings"]:
            if item["status"] == "violated" and item["rule_id"] in rules:
                eligible = db.get(Finding, item["id"])
                break
        if eligible is None:
            no_draft_reason = f"没有符合 {diagnosis['status']} 的 violated Finding（{', '.join(rules)}）"
    else:
        no_draft_reason = (f"诊断 {diagnosis['status']} 没有对应的本地规则，不能自动生成变更单"
                           if preferred_rule is None else no_draft_reason)

    finding_id = None
    change_request_id = None
    status = "no_draft"
    if eligible is not None:
        remediation = _derive_remediation(eligible)
        if not remediation:
            no_draft_reason = f"本地规则 {eligible.rule_id} 无法推导修复方案，需人工填写"
        else:
            rationale = compose_rationale(bundle, eligible.rule_id)
            request = change_requests.create_change_request(
                db, device_id, title=f"外部诊断：{diagnosis['status']} / {eligible.rule_id}",
                rationale=rationale, finding_ids=[eligible.id],
                proposed_commands=remediation, actor=actor)
            finding_id = eligible.id
            change_request_id = request.id
            status = "draft"

    record = DiagnosisImport(
        device_id=device_id,
        bundle_sha256=bundle["bundle_sha256"],
        trace_id=bundle["trace_id"],
        status=status,
        submitted_by=actor,
        diagnosis_status=diagnosis["status"],
        diagnosis_conclusion=diagnosis["conclusion"],
        external_commands=external_commands,
        config_version_id=version.id,
        rule_run_id=run["run_id"],
        finding_id=finding_id,
        change_request_id=change_request_id,
        no_draft_reason=no_draft_reason,
        evidence=_evidence_payload(bundle),
    )
    try:
        db.add(record)
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(DiagnosisImport).where(
            DiagnosisImport.device_id == device_id,
            (DiagnosisImport.bundle_sha256 == bundle["bundle_sha256"])
            | (DiagnosisImport.trace_id == bundle["trace_id"]),
        ))
        if existing is None:
            raise
        return import_payload(existing, duplicate=True)

    return import_payload(record, duplicate=False)


def compose_rationale(bundle: dict[str, Any], rule_id: str) -> str:
    diagnosis = bundle["diagnosis"]
    text = (f"来源 netsecops-agent；诊断 {diagnosis['status']}；bundle {bundle['bundle_sha256'][:16]}；"
            f"trace {bundle['trace_id'][:16]}。外部结论：{diagnosis['conclusion']}。"
            f"本变更单由 netcfg 独立复算规则 {rule_id} 后生成；外部建议命令已存入导入记录供查看，"
            "不作为本变更单的修复方案。")
    return text[:1900]


def _evidence_payload(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = bundle["config_snapshot"]
    citations = bundle.get("citations") or []
    payload = [{
        "evidence_id": item.get("evidence_id"),
        "tool": item.get("tool"),
        "node": item.get("node"),
        "command": item.get("command"),
        "exit_code": item.get("exit_code"),
        "content_sha256": item.get("content_sha256"),
        "excerpt": item.get("excerpt"),
    } for item in citations]
    payload.append({
        "evidence_id": snapshot["evidence_id"],
        "tool": "query_interface_config",
        "node": snapshot["node"],
        "kind": "config_snapshot",
        "content_sha256": snapshot["sha256"],
    })
    return payload


def import_payload(record: DiagnosisImport, *, duplicate: bool = False) -> dict[str, Any]:
    return {
        "id": record.id,
        "device_id": record.device_id,
        "status": record.status,
        "duplicate": duplicate,
        "bundle_sha256": record.bundle_sha256,
        "trace_id": record.trace_id,
        "submitted_by": record.submitted_by,
        "diagnosis_status": record.diagnosis_status,
        "config_version_id": record.config_version_id,
        "rule_run_id": record.rule_run_id,
        "finding_id": record.finding_id,
        "change_request_id": record.change_request_id,
        "no_draft_reason": record.no_draft_reason,
        "external_commands": record.external_commands,
        "submitted_at": record.submitted_at.isoformat(),
    }


def list_imports(db: Session, device_id: str) -> list[DiagnosisImport]:
    _device(db, device_id)
    return list(db.scalars(
        select(DiagnosisImport).where(DiagnosisImport.device_id == device_id)
        .order_by(DiagnosisImport.submitted_at.desc(), DiagnosisImport.id)
    ))


def get_import(db: Session, import_id: str) -> DiagnosisImport:
    record = db.get(DiagnosisImport, import_id)
    if record is None:
        raise ServiceError("IMPORT_NOT_FOUND", "诊断导入记录不存在", 404)
    return record

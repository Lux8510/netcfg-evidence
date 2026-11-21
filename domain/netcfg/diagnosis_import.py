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

from engine.models import ConfigVersion, Device, DeviceLink, DiagnosisImport, Finding

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


def observation_digest(observations: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical(observations)).hexdigest()


def _require(value: Any, condition: bool, message: str) -> None:
    if not condition:
        raise ImportError_(message)


def verify_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    _require(bundle, isinstance(bundle, dict), "bundle must be an object")
    _require(bundle, bundle.get("source") == "netsecops-agent", "bundle source is not netsecops-agent")
    _require(bundle, bundle.get("schema_version") == 3, "unsupported bundle schema version")
    _require(bundle, bundle.get("lab_only") is True, "bundle is not marked lab-only")

    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    _require(bundle, hashlib.sha256(canonical(body)).hexdigest() == bundle.get("bundle_sha256"),
             "bundle hash does not match its contents")

    trace_id = bundle.get("trace_id")
    _require(bundle, isinstance(trace_id, str) and len(trace_id) == 64,
             "bundle has no valid trace_id")

    _require(bundle, isinstance(bundle.get("device"), dict), "device must be an object")
    device = bundle["device"]
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

    link = bundle.get("link")
    _require(bundle, isinstance(link, dict), "bundle has no link")
    _require(bundle, link.get("node") == snapshot["node"], "link node does not match snapshot node")
    _require(bundle, isinstance(link.get("interface"), str) and link["interface"].strip(),
             "link has no local interface")
    _require(bundle, isinstance(link.get("peer"), str) and link["peer"].strip(),
             "link has no peer node")
    _require(bundle, isinstance(link.get("peer_interface"), str) and link["peer_interface"].strip(),
             "link has no peer interface")

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
        _require(bundle, item["evidence_id"] not in by_id, "citation evidence_id is duplicated")
        _require(bundle, isinstance(item.get("tool"), str) and item["tool"], "citation has no tool")
        _require(bundle, isinstance(item.get("node"), str) or item.get("node") is None,
                 "citation node is invalid")
        observations = item.get("observations")
        _require(bundle, isinstance(observations, list) and observations, "citation has no observations")
        for observation in observations:
            _require(bundle, isinstance(observation, dict), "citation observation must be an object")
            _require(bundle, isinstance(observation.get("command"), list), "observation command is invalid")
            _require(bundle, isinstance(observation.get("exit_code"), int), "observation exit_code is invalid")
            _require(bundle, isinstance(observation.get("stdout"), str), "observation stdout is invalid")
            _require(bundle, isinstance(observation.get("stderr"), str), "observation stderr is invalid")
            _require(bundle, isinstance(observation.get("stdout_truncated"), bool), "observation truncation flag missing")
        _require(bundle, observation_digest(observations) == item.get("content_sha256"),
                 "citation digest does not match its observations")
        by_id[item["evidence_id"]] = item

    snapshot_citation = by_id.get(snapshot["evidence_id"])
    _require(bundle, snapshot_citation is not None, "snapshot evidence is missing from citations")
    _require(bundle, snapshot_citation["tool"] == "query_interface_config",
             "snapshot citation is not a running-config observation")
    _require(bundle, snapshot_citation["node"] == snapshot["node"],
             "snapshot citation belongs to another node")
    matching = [obs for obs in snapshot_citation["observations"]
                if obs.get("exit_code") == 0
                and not obs.get("stdout_truncated")
                and any("show running-config" in str(part) for part in obs.get("command") or [])
                and obs.get("stdout") == snapshot["text"]]
    _require(bundle, len(matching) == 1, "snapshot citation has no matching untruncated running-config observation")
    matched = matching[0]
    _require(bundle, matched["command"] == snapshot["command"],
             "snapshot command does not match its citation command")
    _require(bundle, matched["exit_code"] == snapshot["exit_code"],
             "snapshot exit code does not match its citation")

    peer = bundle.get("peer_snapshot")
    if peer is not None:
        _require(bundle, isinstance(peer, dict), "peer_snapshot must be an object")
        _require(bundle, peer.get("node") == link["peer"], "peer snapshot node does not match the link")
        peer_citation = by_id.get(peer.get("evidence_id"))
        _require(bundle, peer_citation is not None, "peer snapshot evidence is missing from citations")
        _require(bundle, peer_citation["tool"] == "query_interface_config",
                 "peer snapshot citation is not a running-config observation")
        _require(bundle, peer_citation["node"] == peer["node"], "peer snapshot citation belongs to another node")
        _require(bundle, hashlib.sha256((peer.get("text") or "").encode("utf-8")).hexdigest() == peer.get("sha256"),
                 "peer snapshot hash does not match")
        matching_peer = [obs for obs in peer_citation["observations"]
                         if obs.get("exit_code") == 0
                         and not obs.get("stdout_truncated")
                         and any("show running-config" in str(part) for part in obs.get("command") or [])
                         and obs.get("stdout") == peer.get("text")]
        _require(bundle, len(matching_peer) == 1,
                 "peer snapshot has no matching untruncated running-config observation")
    return bundle


def _registered_link(db: Session, device_id: str, link: dict[str, Any], peer_id: str) -> bool:
    local_iface = link.get("interface")
    peer_iface = link.get("peer_interface")
    if not local_iface or not peer_iface:
        return False
    row = db.scalar(select(DeviceLink).where(
        ((DeviceLink.device_a_id == device_id) & (DeviceLink.iface_a == local_iface)
         & (DeviceLink.device_b_id == peer_id) & (DeviceLink.iface_b == peer_iface))
        | ((DeviceLink.device_b_id == device_id) & (DeviceLink.iface_b == local_iface)
           & (DeviceLink.device_a_id == peer_id) & (DeviceLink.iface_a == peer_iface))
    ))
    return row is not None


def _iface_from_path(path: str | None) -> str | None:
    parts = (path or "").split(".")
    if len(parts) < 2 or parts[0] != "interface" or not parts[1]:
        return None
    return parts[1]


def _finding_matches_link(db: Session, device_id: str, finding: Finding,
                          link: dict[str, Any], local_version_id: str,
                          expected_peer_version_id: str | None) -> bool:
    """A per-link R005 finding must be about the bundle's link, the peer device
    and both config versions; otherwise the import keeps evidence without a draft."""
    evidence = finding.evidence or []
    if len(evidence) < 2:
        return False
    local, peer = evidence[0], evidence[1]
    local_iface = _iface_from_path(local.get("path"))
    peer_iface = _iface_from_path(peer.get("path"))
    if local_iface != link.get("interface") or peer_iface != link.get("peer_interface"):
        return False
    if local.get("version_id") != local_version_id:
        return False
    peer_version_id = peer.get("version_id")
    if not isinstance(peer_version_id, str) or not peer_version_id:
        return False
    # The peer node must be a registered device on the same link.
    peer_device = db.scalar(select(Device).where(Device.name == link.get("peer")))
    if peer_device is None or peer_device.id == device_id:
        return False
    # The cited peer version must belong to that peer device.
    peer_version = db.get(ConfigVersion, peer_version_id)
    if peer_version is None or peer_version.device_id != peer_device.id:
        return False
    if expected_peer_version_id is None or peer_version.id != expected_peer_version_id:
        return False
    # A registered link must actually connect the two devices on these interfaces.
    link_row = db.scalar(select(DeviceLink).where(
        ((DeviceLink.device_a_id == device_id) & (DeviceLink.iface_a == local_iface)
         & (DeviceLink.device_b_id == peer_device.id) & (DeviceLink.iface_b == peer_iface))
        | ((DeviceLink.device_b_id == device_id) & (DeviceLink.iface_b == local_iface)
           & (DeviceLink.device_a_id == peer_device.id) & (DeviceLink.iface_a == peer_iface))
    ))
    return link_row is not None


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
    device = _device(db, device_id)
    snapshot = bundle["config_snapshot"]
    if device.name != snapshot["node"]:
        raise ServiceError("DEVICE_NODE_MISMATCH",
                           f"目标设备 {device.name} 与包内节点 {snapshot['node']} 不一致；"
                           "请选择名称与该节点一致的设备", 422)

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
    link = bundle["link"]

    version, _created = import_config(db, device_id, snapshot["text"],
                                      f"netsecops-{bundle['bundle_sha256'][:8]}")
    peer_id = peer_version_id = None
    peer_snapshot = bundle.get("peer_snapshot")
    if diagnosis["status"] == "ospf_area_mismatch" and isinstance(peer_snapshot, dict):
        peer = db.scalar(select(Device).where(Device.name == bundle["link"]["peer"]))
        if peer is not None and _registered_link(db, device_id, link, peer.id):
            # Historical evidence only: do not promote it to the peer's current version.
            peer_version, _ = import_config(db, peer.id, peer_snapshot["text"],
                                            f"netsecops-peer-{bundle['bundle_sha256'][:8]}",
                                            historical=True)
            peer_id, peer_version_id = peer.id, peer_version.id
    peer_versions = {peer_id: peer_version_id} if peer_id and peer_version_id else None
    run = run_rules_check(db, device_id, version.id, include_cross=True,
                          peer_versions=peer_versions)
    if run["config_version_id"] != version.id:
        raise ServiceError("RULE_VERSION_MISMATCH", "规则运行未绑定导入的版本", 500)
    if any(item.get("config_version_id") != version.id for item in run["findings"]):
        raise ServiceError("FINDING_VERSION_MISMATCH", "Finding 未绑定导入的版本", 500)

    # Resolve eligibility against the stored ORM finding rows.
    no_draft_reason: str | None = None
    rules = DIAGNOSIS_RULES.get(diagnosis["status"], ())
    if preferred_rule:
        if preferred_rule not in rules:
            no_draft_reason = f"指定规则 {preferred_rule} 不适用于诊断 {diagnosis['status']}"
            rules = ()
        else:
            rules = (preferred_rule,)
    if not rules and no_draft_reason is None:
        no_draft_reason = f"诊断 {diagnosis['status']} 没有对应的本地规则，不能自动生成变更单"

    eligible: Finding | None = None
    if rules:
        for item in run["findings"]:
            if item["status"] != "violated" or item["rule_id"] not in rules:
                continue
            candidate = db.get(Finding, item["id"])
            # Bound the draft to the link the bundle refers to (R005 is per-link).
            if diagnosis["status"] == "ospf_area_mismatch" and not _finding_matches_link(
                    db, device_id, candidate, link, version.id, peer_version_id):
                continue
            eligible = candidate
            break
        if eligible is None:
            no_draft_reason = f"没有符合 {diagnosis['status']} 的 violated Finding（{', '.join(rules)}）"

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
    """Store the verified observations (not a caller-supplied excerpt) so the
    device detail can show text that matches its digest."""
    snapshot = bundle["config_snapshot"]
    citations = bundle.get("citations") or []
    payload = []
    for item in citations:
        observations = item.get("observations") or []
        first = observations[0] if observations else {}
        excerpt = str(first.get("stdout") or "")
        payload.append({
            "evidence_id": item.get("evidence_id"),
            "tool": item.get("tool"),
            "node": item.get("node"),
            "command": first.get("command"),
            "exit_code": first.get("exit_code"),
            "content_sha256": item.get("content_sha256"),
            "excerpt": excerpt[:600],
            "observations": observations,
        })
    peer = bundle.get("peer_snapshot")
    if isinstance(peer, dict):
        payload.append({
            "evidence_id": peer.get("evidence_id"),
            "tool": "query_interface_config",
            "node": peer.get("node"),
            "kind": "peer_snapshot",
            "content_sha256": peer.get("sha256"),
            "observations": [{"command": peer.get("command"), "exit_code": peer.get("exit_code"),
                              "stdout": peer.get("text"), "stderr": ""}],
        })
    payload.append({
        "evidence_id": snapshot["evidence_id"],
        "tool": "query_interface_config",
        "node": snapshot["node"],
        "kind": "config_snapshot",
        "content_sha256": snapshot["sha256"],
        "observations": [{"command": snapshot["command"], "exit_code": snapshot["exit_code"],
                          "stdout": snapshot["text"], "stderr": ""}],
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
        "diagnosis_conclusion": record.diagnosis_conclusion,
        "config_version_id": record.config_version_id,
        "rule_run_id": record.rule_run_id,
        "finding_id": record.finding_id,
        "change_request_id": record.change_request_id,
        "no_draft_reason": record.no_draft_reason,
        "external_commands": record.external_commands,
        "evidence": record.evidence,
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

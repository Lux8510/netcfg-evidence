from __future__ import annotations

import hashlib
import json

import pytest

from domain.netcfg.diagnosis_import import ImportError_, canonical, verify_bundle
from conftest import create_device, import_config


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_bundle(config: str, *, node: str = "r2", status: str = "ospf_area_mismatch",
                trace_id: str = "a" * 64, commands: list[str] | None = None,
                evidence_id: str = "E007", citations: list[dict] | None = None) -> dict:
    snapshot_sha = _sha(config)
    if citations is None:
        citations = [{
            "evidence_id": evidence_id, "tool": "query_interface_config",
            "arguments": {"node": node, "interface": "eth1"}, "node": node,
            "command": ["vtysh", "-c", "show running-config"], "exit_code": 0,
            "content_sha256": snapshot_sha, "excerpt": config[:120],
        }]
    bundle = {
        "schema_version": 2,
        "source": "netsecops-agent",
        "trace_id": trace_id,
        "created_at_utc": "2026-11-20T00:00:00+00:00",
        "device": {"name": node},
        "diagnosis": {"status": status, "conclusion": "结论", "repair_suggestion": "建议",
                       "evidence_ids": [evidence_id]},
        "config_snapshot": {
            "node": node, "target": node, "evidence_id": evidence_id,
            "command": ["vtysh", "-c", "show running-config"], "exit_code": 0,
            "text": config, "sha256": snapshot_sha,
        },
        "proposed_commands": commands if commands is not None
        else ["no ip ospf area 0.0.0.1", "ip ospf area 0.0.0.0"],
        "citations": citations,
        "lab_only": True,
    }
    bundle["bundle_sha256"] = hashlib.sha256(canonical(bundle)).hexdigest()
    return bundle


def test_bundle_hash_and_snapshot_are_verified(r1_text):
    bundle = verify_bundle(make_bundle(r1_text))
    assert bundle["bundle_sha256"]
    bundle["diagnosis"]["status"] = "normal"
    with pytest.raises(ImportError_):
        verify_bundle(bundle)


def test_import_rejects_unknown_schema_and_bad_digests(r1_text):
    bundle = make_bundle(r1_text)
    bundle["schema_version"] = 1
    with pytest.raises(ImportError_):
        verify_bundle(bundle)
    bundle = make_bundle(r1_text)
    bundle["config_snapshot"]["text"] += "\n!"
    with pytest.raises(ImportError_):
        verify_bundle(bundle)


def test_import_rejects_node_target_or_evidence_tampering(r1_text):
    bundle = make_bundle(r1_text)
    bundle["config_snapshot"]["target"] = "r1"
    bundle["device"]["name"] = "r1"
    with pytest.raises(ImportError_):
        verify_bundle(bundle)

    bundle = make_bundle(r1_text)
    bundle["config_snapshot"]["evidence_id"] = ""
    with pytest.raises(ImportError_):
        verify_bundle(bundle)

    bundle = make_bundle(r1_text)
    bundle["citations"] = []
    with pytest.raises(ImportError_):
        verify_bundle(bundle)


def _area_mismatch_setup(app_client, r1_text, r2_text):
    """Register r1 (peer) and r2 (target) with a link, returning their ids."""
    a = create_device(app_client, "peer-r1")
    b = create_device(app_client, "target-r2")
    import_config(app_client, a, r1_text)
    mismatched = r2_text.replace("network 10.12.0.0/30 area 0.0.0.0",
                                 "network 10.12.0.0/30 area 0.0.0.1")
    import_config(app_client, b, mismatched)
    link = app_client.post("/api/links", json={
        "device_a_id": a, "iface_a": "eth2", "device_b_id": b, "iface_b": "eth1"})
    assert link.status_code == 201, link.text
    return a, b, mismatched


def test_import_creates_draft_from_relevant_local_finding(app_client, r1_text, r2_text):
    _a, target, mismatched = _area_mismatch_setup(app_client, r1_text, r2_text)
    bundle = make_bundle(mismatched, node="r2", status="ospf_area_mismatch",
                         commands=["no ip ospf area 0.0.0.1", "ip ospf area 0.0.0.0"])
    response = app_client.post(f"/api/devices/{target}/diagnosis-imports", json={"bundle": bundle})
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert result["status"] == "draft"
    assert result["finding_id"] and result["change_request_id"]

    # The draft must be based on the local R005 finding, not the external commands.
    change = app_client.get(f"/api/change-requests/{result['change_request_id']}").json()["data"]
    assert change["proposed_commands"] == ["interface eth1", "ip ospf area 0.0.0.0"]
    assert "ip ospf area 0.0.0.1" not in change["proposed_commands"]
    finding = app_client.get(f"/api/devices/{target}/findings",
                             params={"run_id": result["rule_run_id"]}).json()["data"]
    linked = next(item for item in finding if item["id"] == result["finding_id"])
    assert linked["rule_id"] == "R005"

    # External commands are preserved on the import record, not in the draft.
    record = app_client.get(f"/api/diagnosis-imports/{result['id']}").json()["data"]
    assert record["external_commands"] == ["no ip ospf area 0.0.0.1", "ip ospf area 0.0.0.0"]


def test_irrelevant_violation_saves_evidence_without_draft(app_client, r1_text):
    device = create_device(app_client, "irrelevant")
    broken = r1_text.replace(" passive-interface lo\n", "")
    bundle = make_bundle(broken, node="r2", status="interface_admin_down")
    response = app_client.post(f"/api/devices/{device}/diagnosis-imports", json={"bundle": bundle})
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert result["status"] == "no_draft"
    assert result["change_request_id"] is None
    assert result["no_draft_reason"]


def test_normal_or_insufficient_saves_evidence_without_draft(app_client, r1_text):
    device = create_device(app_client, "normal-import")
    for status in ("normal", "insufficient_evidence", "collection_failure"):
        bundle = make_bundle(r1_text, node="r2", status=status, trace_id=("b" * 64 if status == "normal" else "c" * 64 if status == "insufficient_evidence" else "d" * 64))
        response = app_client.post(f"/api/devices/{device}/diagnosis-imports", json={"bundle": bundle})
        assert response.status_code == 201, response.text
        assert response.json()["data"]["status"] == "no_draft"


def test_idempotent_reimport_returns_same_ids(app_client, r1_text, r2_text):
    _a, target, mismatched = _area_mismatch_setup(app_client, r1_text, r2_text)
    bundle = make_bundle(mismatched, node="r2", status="ospf_area_mismatch")
    first = app_client.post(f"/api/devices/{target}/diagnosis-imports",
                            json={"bundle": bundle}).json()["data"]
    second = app_client.post(f"/api/devices/{target}/diagnosis-imports",
                             json={"bundle": bundle}).json()["data"]
    assert first["id"] == second["id"]
    assert first["config_version_id"] == second["config_version_id"]
    assert first["rule_run_id"] == second["rule_run_id"]
    assert first["change_request_id"] == second["change_request_id"]
    assert second["duplicate"] is True
    # No extra business rows.
    versions = app_client.get(f"/api/devices/{target}/configs").json()["data"]
    assert len(versions) == 1
    runs = app_client.get(f"/api/devices/{target}/rules/runs").json()["data"]
    assert len(runs) == 1
    changes = app_client.get(f"/api/devices/{target}/change-requests").json()["data"]
    assert len(changes) == 1


def test_reexport_same_trace_dedups_by_trace_id(app_client, r1_text):
    device = create_device(app_client, "reexport")
    bundle = make_bundle(r1_text, node="r2", status="normal", trace_id="e" * 64)
    first = app_client.post(f"/api/devices/{device}/diagnosis-imports",
                            json={"bundle": bundle}).json()["data"]
    # Re-export: same trace, new bundle hash (created_at differs).
    rebundled = make_bundle(r1_text, node="r2", status="normal", trace_id="e" * 64)
    rebundled["created_at_utc"] = "2026-11-20T00:01:00+00:00"
    body = {key: value for key, value in rebundled.items() if key != "bundle_sha256"}
    rebundled["bundle_sha256"] = hashlib.sha256(canonical(body)).hexdigest()
    second = app_client.post(f"/api/devices/{device}/diagnosis-imports",
                             json={"bundle": rebundled}).json()["data"]
    assert first["id"] == second["id"]
    assert second["duplicate"] is True


def test_import_does_not_move_an_existing_baseline(app_client, r1_text):
    device = create_device(app_client, "baseline-guard")
    baseline_version = import_config(app_client, device, r1_text).json()["data"]
    app_client.post(f"/api/devices/{device}/baseline", json={"version_id": baseline_version["id"]})
    before = app_client.get(f"/api/devices/{device}/baseline").json()["data"]

    changed = r1_text.replace(" ip ospf cost 10", " ip ospf cost 30")
    bundle = make_bundle(changed, node="r2", status="normal", trace_id="f" * 64)
    app_client.post(f"/api/devices/{device}/diagnosis-imports", json={"bundle": bundle})
    after = app_client.get(f"/api/devices/{device}/baseline").json()["data"]
    assert after["config_version_id"] == before["config_version_id"]


def test_snapshot_device_mismatch_is_rejected(app_client, r1_text):
    device = create_device(app_client, "mismatch-reject")
    bundle = make_bundle(r1_text, node="r2", status="normal", trace_id="a1" + "0" * 62)
    bundle["device"]["name"] = "r1"  # device name no longer matches snapshot node
    bundle["bundle_sha256"] = hashlib.sha256(canonical(bundle)).hexdigest()
    response = app_client.post(f"/api/devices/{device}/diagnosis-imports", json={"bundle": bundle})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_BUNDLE"
    assert app_client.get(f"/api/devices/{device}/configs").json()["data"] == []
    assert app_client.get(f"/api/devices/{device}/rules/runs").json()["data"] == []
    assert app_client.get(f"/api/devices/{device}/change-requests").json()["data"] == []
    assert app_client.get(f"/api/devices/{device}/diagnosis-imports").json()["data"] == []


def test_record_is_traceable_from_device_detail(app_client, r1_text):
    device = create_device(app_client, "traceable")
    bundle = make_bundle(r1_text, node="r2", status="normal", trace_id="b1" + "0" * 62)
    result = app_client.post(f"/api/devices/{device}/diagnosis-imports",
                             json={"bundle": bundle}).json()["data"]
    rows = app_client.get(f"/api/devices/{device}/diagnosis-imports").json()["data"]
    assert [row["id"] for row in rows] == [result["id"]]
    row = rows[0]
    assert row["submitted_by"] == "何深"
    assert row["config_version_id"] and row["rule_run_id"]
    assert row["diagnosis_status"] == "normal"

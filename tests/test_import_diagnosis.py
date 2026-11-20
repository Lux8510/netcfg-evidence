from __future__ import annotations

import hashlib
import json

import pytest

from conftest import create_device, import_config
from domain.netcfg.diagnosis_import import ImportError_, canonical, observation_digest, verify_bundle


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _obs(node: str, command: list[str], stdout: str, *, exit_code: int = 0) -> dict:
    return {"node": node, "command": command, "exit_code": exit_code, "stdout": stdout,
            "stderr": "", "stdout_truncated": False, "stderr_truncated": False,
            "stdout_bytes": len(stdout.encode()), "stderr_bytes": 0}


def _citation(eid: str, node: str, observations: list[dict], *,
              tool: str = "query_interface_config") -> dict:
    return {"evidence_id": eid, "tool": tool, "arguments": {"node": node}, "node": node,
            "observations": observations, "content_sha256": observation_digest(observations)}


def make_bundle(config: str, *, node: str = "r2", status: str = "normal",
                trace_id: str = "a" * 64, peer_config: str | None = None,
                peer_node: str = "r1", peer_interface: str = "eth2",
                commands: list[str] | None = None, evidence_id: str = "E1") -> dict:
    snapshot_sha = _sha(config)
    snapshot_obs = _obs(node, ["vtysh", "-c", "show running-config"], config)
    citations = [_citation(evidence_id, node, [snapshot_obs])]
    peer_snapshot = None
    if peer_config is not None:
        peer_sha = _sha(peer_config)
        peer_obs = _obs(peer_node, ["vtysh", "-c", "show running-config"], peer_config)
        citations.append(_citation("PEER1", peer_node, [peer_obs]))
        peer_snapshot = {"node": peer_node, "interface": peer_interface,
                         "evidence_id": "PEER1", "command": ["vtysh", "-c", "show running-config"],
                         "exit_code": 0, "text": peer_config, "sha256": peer_sha}
    bundle = {
        "schema_version": 3,
        "source": "netsecops-agent",
        "trace_id": trace_id,
        "created_at_utc": "2026-11-20T00:00:00+00:00",
        "device": {"name": node},
        "diagnosis": {"status": status, "conclusion": "两端 Area 不一致",
                       "repair_suggestion": None, "evidence_ids": [evidence_id]},
        "config_snapshot": {"node": node, "target": node, "evidence_id": evidence_id,
                            "command": ["vtysh", "-c", "show running-config"], "exit_code": 0,
                            "text": config, "sha256": snapshot_sha},
        "peer_snapshot": peer_snapshot,
        "proposed_commands": commands if commands is not None else ["no ip ospf area 0.0.0.1"],
        "citations": citations,
        "link": {"node": node, "interface": "eth1", "peer": peer_node,
                 "peer_interface": peer_interface},
        "lab_only": True,
    }
    bundle["bundle_sha256"] = hashlib.sha256(canonical(bundle)).hexdigest()
    return bundle


def _rehash(bundle: dict) -> dict:
    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    bundle["bundle_sha256"] = hashlib.sha256(canonical(body)).hexdigest()
    return bundle


def _area_setup(app_client, r1_text, r2_text):
    r1 = create_device(app_client, "r1")
    r2 = create_device(app_client, "r2")
    import_config(app_client, r1, r1_text)
    local = r2_text.replace("network 10.12.0.0/30 area 0.0.0.0",
                            "network 10.12.0.0/30 area 0.0.0.1")
    import_config(app_client, r2, local)
    app_client.post("/api/links", json={
        "device_a_id": r1, "iface_a": "eth2", "device_b_id": r2, "iface_b": "eth1"})
    return r1, r2, local


# ---------------- bundle validation ----------------

def test_bundle_schema_digest_and_snapshot(r1_text):
    bundle = verify_bundle(make_bundle(r1_text))
    assert bundle["schema_version"] == 3

    bad = make_bundle(r1_text)
    bad["schema_version"] = 2
    with pytest.raises(ImportError_):
        verify_bundle(bad)

    tampered = make_bundle(r1_text)
    tampered["citations"][0]["observations"][0]["stdout"] += "\n!"
    with pytest.raises(ImportError_):
        verify_bundle(tampered)


def test_display_text_cannot_diverge_from_digest(r1_text):
    bundle = make_bundle(r1_text)
    bundle["citations"][0]["observations"][0]["stdout"] = "replaced text"
    _rehash(bundle)
    with pytest.raises(ImportError_):
        verify_bundle(bundle)


def test_snapshot_command_must_match_citation(r1_text):
    bundle = make_bundle(r1_text)
    bundle["config_snapshot"]["command"] = ["vtysh", "-c", "show version"]
    _rehash(bundle)
    with pytest.raises(ImportError_):
        verify_bundle(bundle)


def test_peer_snapshot_identity_is_checked(r1_text, r2_text):
    bundle = make_bundle(r2_text, status="ospf_area_mismatch", peer_config=r1_text)
    bundle["peer_snapshot"]["node"] = "different"
    _rehash(bundle)
    with pytest.raises(ImportError_):
        verify_bundle(bundle)


def test_duplicate_evidence_id_is_rejected(r1_text):
    bundle = make_bundle(r1_text)
    bundle["citations"].append(dict(bundle["citations"][0]))
    _rehash(bundle)
    with pytest.raises(ImportError_):
        verify_bundle(bundle)


# ---------------- identity and binding ----------------

def test_wrong_device_node_binding_is_rejected(app_client, r1_text):
    unrelated = create_device(app_client, "unrelated")
    bundle = make_bundle(r1_text, node="r2", trace_id="10" + "0" * 62)
    response = app_client.post(f"/api/devices/{unrelated}/diagnosis-imports",
                               json={"bundle": bundle})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DEVICE_NODE_MISMATCH"
    assert app_client.get(f"/api/devices/{unrelated}/configs").json()["data"] == []
    assert app_client.get(f"/api/devices/{unrelated}/rules/runs").json()["data"] == []
    assert app_client.get(f"/api/devices/{unrelated}/change-requests").json()["data"] == []
    assert app_client.get(f"/api/devices/{unrelated}/diagnosis-imports").json()["data"] == []


def test_malformed_device_is_422(app_client, r1_text):
    device = create_device(app_client, "r2")
    bundle = make_bundle(r1_text, node="r2", trace_id="11" + "0" * 62)
    bundle["device"] = "r2"
    _rehash(bundle)
    response = app_client.post(f"/api/devices/{device}/diagnosis-imports", json={"bundle": bundle})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_BUNDLE"


# ---------------- draft eligibility ----------------

def test_area_import_creates_draft_from_pinned_peer_snapshot(app_client, r1_text, r2_text):
    _r1, r2, local = _area_setup(app_client, r1_text, r2_text)
    bundle = make_bundle(local, node="r2", status="ospf_area_mismatch",
                         peer_config=r1_text, trace_id="12" + "0" * 62)
    result = app_client.post(f"/api/devices/{r2}/diagnosis-imports",
                             json={"bundle": bundle}).json()["data"]
    assert result["status"] == "draft"
    change = app_client.get(f"/api/change-requests/{result['change_request_id']}").json()["data"]
    assert change["proposed_commands"] == ["interface eth1", "ip ospf area 0.0.0.0"]
    record = app_client.get(f"/api/diagnosis-imports/{result['id']}").json()["data"]
    assert record["external_commands"] == ["no ip ospf area 0.0.0.1"]


def test_area_without_peer_snapshot_only_stores_evidence(app_client, r1_text, r2_text):
    _r1, r2, local = _area_setup(app_client, r1_text, r2_text)
    bundle = make_bundle(local, node="r2", status="ospf_area_mismatch",
                         trace_id="13" + "0" * 62)
    result = app_client.post(f"/api/devices/{r2}/diagnosis-imports",
                             json={"bundle": bundle}).json()["data"]
    assert result["status"] == "no_draft"
    assert result["change_request_id"] is None


def test_draft_uses_peer_snapshot_not_latest_peer_version(app_client, r1_text, r2_text):
    r1, r2, local = _area_setup(app_client, r1_text, r2_text)
    # Move the peer's latest config to a third area; the bundle carries the original 0.0.0.0.
    import_config(app_client, r1, r1_text.replace("network 10.12.0.0/30 area 0.0.0.0",
                                                  "network 10.12.0.0/30 area 0.0.0.2"))
    bundle = make_bundle(local, node="r2", status="ospf_area_mismatch",
                         peer_config=r1_text, trace_id="14" + "0" * 62)
    result = app_client.post(f"/api/devices/{r2}/diagnosis-imports",
                             json={"bundle": bundle}).json()["data"]
    assert result["status"] == "draft"
    change = app_client.get(f"/api/change-requests/{result['change_request_id']}").json()["data"]
    assert change["proposed_commands"] == ["interface eth1", "ip ospf area 0.0.0.0"]


def test_peer_identity_mismatch_does_not_create_draft(app_client, r1_text, r2_text):
    r1, r2, local = _area_setup(app_client, r1_text, r2_text)
    bundle = make_bundle(local, node="r2", status="ospf_area_mismatch",
                         peer_config=r1_text, trace_id="15" + "0" * 62)
    bundle["link"]["peer"] = "different"
    bundle["link"]["peer_interface"] = "eth9"
    _rehash(bundle)
    # peer_snapshot node no longer matches link.peer -> rejected
    response = app_client.post(f"/api/devices/{r2}/diagnosis-imports", json={"bundle": bundle})
    assert response.status_code == 422


def test_unrelated_violation_only_stores_evidence(app_client, r1_text):
    device = create_device(app_client, "r2")
    broken = r1_text.replace(" passive-interface lo\n", "")
    bundle = make_bundle(broken, node="r2", status="interface_admin_down",
                         trace_id="16" + "0" * 62)
    result = app_client.post(f"/api/devices/{device}/diagnosis-imports",
                             json={"bundle": bundle}).json()["data"]
    assert result["status"] == "no_draft"
    assert result["no_draft_reason"]


def test_preferred_rule_outside_mapping_sets_reason(app_client, r1_text, r2_text):
    _r1, r2, local = _area_setup(app_client, r1_text, r2_text)
    bundle = make_bundle(local, node="r2", status="ospf_area_mismatch",
                         peer_config=r1_text, trace_id="17" + "0" * 62)
    result = app_client.post(f"/api/devices/{r2}/diagnosis-imports",
                             json={"bundle": bundle, "preferred_rule": "R001"}).json()["data"]
    assert result["status"] == "no_draft"
    assert result["no_draft_reason"] and "R001" in result["no_draft_reason"]


# ---------------- idempotency and transactions ----------------

def test_idempotent_reimport_and_trace_dedup(app_client, r1_text, r2_text):
    _r1, r2, local = _area_setup(app_client, r1_text, r2_text)
    bundle = make_bundle(local, node="r2", status="ospf_area_mismatch",
                         peer_config=r1_text, trace_id="18" + "0" * 62)
    first = app_client.post(f"/api/devices/{r2}/diagnosis-imports",
                            json={"bundle": bundle}).json()["data"]
    second = app_client.post(f"/api/devices/{r2}/diagnosis-imports",
                             json={"bundle": bundle}).json()["data"]
    assert first["id"] == second["id"] and second["duplicate"] is True
    assert len(app_client.get(f"/api/devices/{r2}/configs").json()["data"]) == 1
    assert len(app_client.get(f"/api/devices/{r2}/rules/runs").json()["data"]) == 1
    assert len(app_client.get(f"/api/devices/{r2}/change-requests").json()["data"]) == 1
    assert len(app_client.get(f"/api/devices/{r2}/diagnosis-imports").json()["data"]) == 1

    rebundled = make_bundle(local, node="r2", status="ospf_area_mismatch",
                            peer_config=r1_text, trace_id="18" + "0" * 62)
    rebundled["created_at_utc"] = "2026-11-20T00:01:00+00:00"
    _rehash(rebundled)
    third = app_client.post(f"/api/devices/{r2}/diagnosis-imports",
                            json={"bundle": rebundled}).json()["data"]
    assert third["id"] == first["id"]


def test_failure_after_draft_rolls_back_all_records(app_client, monkeypatch, r1_text, r2_text):
    import domain.netcfg.diagnosis_import as di
    from domain.netcfg.service import ServiceError

    r1 = create_device(app_client, "r1")
    fresh = create_device(app_client, "r2-fresh")
    import_config(app_client, r1, r1_text)
    local = r2_text.replace("network 10.12.0.0/30 area 0.0.0.0",
                            "network 10.12.0.0/30 area 0.0.0.1")
    app_client.post("/api/links", json={
        "device_a_id": r1, "iface_a": "eth2", "device_b_id": fresh, "iface_b": "eth1"})
    bundle = make_bundle(local, node="r2-fresh", status="ospf_area_mismatch",
                         peer_config=r1_text, trace_id="19" + "0" * 62)

    original_init = di.DiagnosisImport.__init__
    def boom(self, *args, **kwargs):
        raise ServiceError("INJECTED", "注入失败", 422)
    monkeypatch.setattr(di.DiagnosisImport, "__init__", boom)
    response = app_client.post(f"/api/devices/{fresh}/diagnosis-imports", json={"bundle": bundle})
    assert response.status_code == 422
    assert app_client.get(f"/api/devices/{fresh}/configs").json()["data"] == []
    assert app_client.get(f"/api/devices/{fresh}/rules/runs").json()["data"] == []
    assert app_client.get(f"/api/devices/{fresh}/change-requests").json()["data"] == []
    assert app_client.get(f"/api/devices/{fresh}/diagnosis-imports").json()["data"] == []

    monkeypatch.setattr(di.DiagnosisImport, "__init__", original_init)
    retry = app_client.post(f"/api/devices/{fresh}/diagnosis-imports", json={"bundle": bundle})
    assert retry.status_code == 201, retry.text
    assert retry.json()["data"]["status"] == "draft"
    assert len(app_client.get(f"/api/devices/{fresh}/configs").json()["data"]) == 1
    assert len(app_client.get(f"/api/devices/{fresh}/rules/runs").json()["data"]) == 1
    assert len(app_client.get(f"/api/devices/{fresh}/change-requests").json()["data"]) == 1
    assert len(app_client.get(f"/api/devices/{fresh}/diagnosis-imports").json()["data"]) == 1


def test_baseline_is_never_moved(app_client, r1_text):
    device = create_device(app_client, "r2")
    baseline = import_config(app_client, device, r1_text).json()["data"]
    app_client.post(f"/api/devices/{device}/baseline", json={"version_id": baseline["id"]})
    before = app_client.get(f"/api/devices/{device}/baseline").json()["data"]
    bundle = make_bundle(r1_text.replace(" ip ospf cost 10", " ip ospf cost 30"),
                         node="r2", trace_id="20" + "0" * 62)
    app_client.post(f"/api/devices/{device}/diagnosis-imports", json={"bundle": bundle})
    after = app_client.get(f"/api/devices/{device}/baseline").json()["data"]
    assert after["config_version_id"] == before["config_version_id"]


def test_record_is_traceable_and_stores_observations(app_client, r1_text):
    device = create_device(app_client, "r2")
    bundle = make_bundle(r1_text, node="r2", trace_id="21" + "0" * 62)
    result = app_client.post(f"/api/devices/{device}/diagnosis-imports",
                             json={"bundle": bundle}).json()["data"]
    rows = app_client.get(f"/api/devices/{device}/diagnosis-imports").json()["data"]
    assert [row["id"] for row in rows] == [result["id"]]
    row = rows[0]
    assert row["submitted_by"] == "何深"
    assert row["config_version_id"] and row["rule_run_id"]
    assert row["evidence"] and row["evidence"][0]["observations"]
    assert row["evidence"][0]["excerpt"] == r1_text[:600]

from __future__ import annotations

import hashlib

from conftest import import_config
from domain.netcfg.diagnosis_import import canonical, observation_digest


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bundle(config: str, *, node: str = "r2", status: str = "normal",
            peer_config: str | None = None, trace_id: str = "a" * 64) -> dict:
    def obs(n: str, text: str) -> dict:
        return {"node": n, "command": ["vtysh", "-c", "show running-config"], "exit_code": 0,
                "stdout": text, "stderr": "", "stdout_truncated": False,
                "stderr_truncated": False, "stdout_bytes": len(text.encode()), "stderr_bytes": 0}

    def citation(eid: str, n: str, text: str) -> dict:
        observations = [obs(n, text)]
        return {"evidence_id": eid, "tool": "query_interface_config",
                "arguments": {"node": n, "interface": "eth1"}, "node": n,
                "observations": observations,
                "content_sha256": observation_digest(observations)}

    citations = [citation("E1", node, config)]
    peer_snapshot = None
    if peer_config is not None:
        citations.append(citation("PEER", "r1", peer_config))
        peer_snapshot = {"node": "r1", "interface": "eth2", "evidence_id": "PEER",
                         "command": ["vtysh", "-c", "show running-config"], "exit_code": 0,
                         "text": peer_config, "sha256": _sha(peer_config)}
    bundle = {
        "schema_version": 3, "source": "netsecops-agent", "trace_id": trace_id,
        "created_at_utc": "2026-11-21T00:00:00+00:00", "device": {"name": node},
        "diagnosis": {"status": status, "conclusion": "结论", "repair_suggestion": None,
                       "evidence_ids": ["E1"]},
        "config_snapshot": {"node": node, "target": node, "evidence_id": "E1",
                            "command": ["vtysh", "-c", "show running-config"], "exit_code": 0,
                            "text": config, "sha256": _sha(config)},
        "peer_snapshot": peer_snapshot,
        "proposed_commands": ["no ip ospf area 0.0.0.1"],
        "citations": citations,
        "link": {"node": node, "interface": "eth1", "peer": "r1", "peer_interface": "eth2"},
        "lab_only": True,
    }
    bundle["bundle_sha256"] = hashlib.sha256(canonical(bundle)).hexdigest()
    return bundle


def _device(client, name: str) -> str:
    response = client.post("/api/devices", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _map(client, environment: str, topology: str, node: str, device_id: str):
    return client.post("/api/mappings", json={
        "environment": environment, "topology": topology, "node": node, "device_id": device_id})


def _post_import(client, device_id: str, bundle: dict, environment: str, topology: str):
    return client.post(f"/api/devices/{device_id}/diagnosis-imports", json={
        "bundle": bundle, "environment": environment, "topology": topology})


def test_same_node_in_two_environments_binds_two_devices(app_client, r1_text):
    a = _device(app_client, "r2-a")
    b = _device(app_client, "r2-b")
    assert _map(app_client, "env-a", "core", "r2", a).status_code == 201
    assert _map(app_client, "env-b", "core", "r2", b).status_code == 201

    bundle_a = _bundle(r1_text, node="r2", trace_id="1" * 64)
    bundle_b = _bundle(r1_text, node="r2", trace_id="2" * 64)
    first = _post_import(app_client, a, bundle_a, "env-a", "core")
    second = _post_import(app_client, b, bundle_b, "env-b", "core")
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["data"]["environment"] == "env-a"
    assert second.json()["data"]["environment"] == "env-b"
    assert first.json()["data"]["device_id"] != second.json()["data"]["device_id"]


def test_alias_device_mapping_is_accepted(app_client, r1_text):
    alias = _device(app_client, "r2-production")
    assert _map(app_client, "prod", "core", "r2", alias).status_code == 201
    response = _post_import(app_client, alias, _bundle(r1_text, trace_id="3" * 64), "prod", "core")
    assert response.status_code == 201, response.text
    assert response.json()["data"]["device_id"] == alias


def test_wrong_environment_is_rejected_without_writes(app_client, r1_text):
    device = _device(app_client, "r2")
    assert _map(app_client, "env-a", "core", "r2", device).status_code == 201
    response = _post_import(app_client, device, _bundle(r1_text, trace_id="4" * 64), "env-b", "core")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MAPPING_NOT_FOUND"
    assert app_client.get(f"/api/devices/{device}/configs").json()["data"] == []
    assert app_client.get(f"/api/devices/{device}/diagnosis-imports").json()["data"] == []


def test_mapping_to_another_device_is_rejected(app_client, r1_text):
    owner = _device(app_client, "r2-owner")
    other = _device(app_client, "other")
    assert _map(app_client, "lab", "core", "r2", owner).status_code == 201
    response = _post_import(app_client, other, _bundle(r1_text, trace_id="5" * 64), "lab", "core")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MAPPING_DEVICE_MISMATCH"
    assert app_client.get(f"/api/devices/{other}/diagnosis-imports").json()["data"] == []


def test_mapping_conflict_and_deactivation(app_client, r1_text):
    device = _device(app_client, "r2")
    first = _map(app_client, "lab", "core", "r2", device)
    assert first.status_code == 201
    conflict = _map(app_client, "lab", "core", "r2", device)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "MAPPING_EXISTS"
    mapping_id = first.json()["data"]["id"]
    assert app_client.delete(f"/api/mappings/{mapping_id}").status_code == 204
    again = _map(app_client, "lab", "core", "r2", device)
    assert again.status_code == 201


def test_import_requires_explicit_environment_and_topology(app_client, r1_text):
    device = _device(app_client, "r2")
    response = app_client.post(f"/api/devices/{device}/diagnosis-imports",
                               json={"bundle": _bundle(r1_text, trace_id="6" * 64)})
    assert response.status_code == 422
    assert app_client.get(f"/api/devices/{device}/diagnosis-imports").json()["data"] == []


def test_r005_peer_is_resolved_in_the_same_environment(app_client, r1_text, r2_text):
    peer = _device(app_client, "peer-production")
    target = _device(app_client, "target-production")
    assert _map(app_client, "prod", "core", "r1", peer).status_code == 201
    assert _map(app_client, "prod", "core", "r2", target).status_code == 201
    app_client.post("/api/links", json={
        "device_a_id": peer, "iface_a": "eth2", "device_b_id": target, "iface_b": "eth1"})
    local = r2_text.replace("network 10.12.0.0/30 area 0.0.0.0",
                            "network 10.12.0.0/30 area 0.0.0.1")
    bundle = _bundle(local, node="r2", status="ospf_area_mismatch",
                     peer_config=r1_text, trace_id="7" * 64)
    response = _post_import(app_client, target, bundle, "prod", "core")
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert result["status"] == "draft"
    change = app_client.get(f"/api/change-requests/{result['change_request_id']}").json()["data"]
    assert change["finding_ids"] == [result["finding_id"]]
    assert change["proposed_commands"] == ["interface eth1", "ip ospf area 0.0.0.0"]


def test_peer_mapping_missing_only_stores_evidence(app_client, r1_text, r2_text):
    target = _device(app_client, "target")
    assert _map(app_client, "prod", "core", "r2", target).status_code == 201
    local = r2_text.replace("network 10.12.0.0/30 area 0.0.0.0",
                            "network 10.12.0.0/30 area 0.0.0.1")
    response = _post_import(app_client, target, _bundle(local, node="r2", status="ospf_area_mismatch",
                                                       peer_config=r1_text, trace_id="8" * 64),
                            "prod", "core")
    assert response.status_code == 201, response.text
    assert response.json()["data"]["status"] == "no_draft"
    assert "对端节点" in (response.json()["data"]["no_draft_reason"] or "")

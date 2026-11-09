from __future__ import annotations

from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parents[1] / "samples/frr"


@pytest.fixture(scope="module")
def r1_text() -> str:
    return (SAMPLES / "r1.conf").read_text(encoding="utf-8")


@pytest.fixture()
def broken_finding(app_client, r1_text):
    device_id = app_client.post("/api/devices", json={"name": "r1"}).json()["data"]["id"]
    text = r1_text.replace(" ip ospf cost 10\n", "").replace(" passive-interface lo\n", "")
    app_client.post(f"/api/devices/{device_id}/configs", json={"text": text, "source_name": "r1.conf"})
    result = app_client.post(f"/api/devices/{device_id}/rules/check", json={}).json()["data"]
    return device_id, result


def create_payload(finding_ids, **overrides):
    payload = {
        "title": "补回 eth2 的 OSPF cost",
        "rationale": "R001 要求点到点接口显式设置 cost，当前配置缺失会依赖默认值。",
        "finding_ids": finding_ids,
        "proposed_commands": ["interface eth2", " ip ospf cost 10"],
        "actor": "何深",
    }
    payload.update(overrides)
    return payload


def test_create_submit_approve_apply(app_client, broken_finding):
    device_id, result = broken_finding
    finding_ids = [item["id"] for item in result["findings"]]
    created = app_client.post(f"/api/devices/{device_id}/change-requests", json=create_payload(finding_ids))
    assert created.status_code == 201, created.text
    request = created.json()["data"]
    assert request["status"] == "draft"
    assert request["executed_on_device"] is False
    assert len(request["findings_snapshot"]) == len(finding_ids)
    assert request["findings_snapshot"][0]["evidence"]

    request_id = request["id"]
    assert app_client.post(f"/api/change-requests/{request_id}/submit",
                           json={"actor": "何深", "note": "提交审批"}).json()["data"]["status"] == "pending_approval"
    approved = app_client.post(f"/api/change-requests/{request_id}/approve",
                               json={"actor": "主管", "note": "已核对原票与规则", "idempotency_key": "approve-1"})
    assert approved.status_code == 200, approved.text
    data = approved.json()["data"]
    assert data["status"] == "approved" and data["approved_by"] == "何深"
    assert data["executed_on_device"] is False

    applied = app_client.post(f"/api/change-requests/{request_id}/apply",
                              json={"actor": "何深", "note": "已在维护窗口执行"})
    assert applied.json()["data"]["status"] == "applied"
    assert applied.json()["data"]["applied_at"]

    events = app_client.get(f"/api/change-requests/{request_id}/events").json()["data"]
    assert [event["action"] for event in events] == ["created", "submit", "approve", "apply"]
    assert [event["actor"] for event in events] == ["何深", "何深", "何深", "何深"]
    assert events[2]["snapshot"]["status"] == "approved"


def test_invalid_transition_is_rejected(app_client, broken_finding):
    device_id, result = broken_finding
    request_id = app_client.post(f"/api/devices/{device_id}/change-requests",
                                 json=create_payload([result["findings"][0]["id"]])).json()["data"]["id"]
    response = app_client.post(f"/api/change-requests/{request_id}/approve",
                               json={"actor": "主管", "note": "跳过提交直接批准"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_TRANSITION"


def test_reject_is_terminal_and_records_reason(app_client, broken_finding):
    device_id, result = broken_finding
    request_id = app_client.post(f"/api/devices/{device_id}/change-requests",
                                 json=create_payload([result["findings"][0]["id"]])).json()["data"]["id"]
    app_client.post(f"/api/change-requests/{request_id}/submit", json={"actor": "何深", "note": "提交"})
    rejected = app_client.post(f"/api/change-requests/{request_id}/reject",
                               json={"actor": "主管", "note": "维护窗口不允许改路由"})
    assert rejected.json()["data"]["status"] == "rejected"
    assert rejected.json()["data"]["rejection_reason"] == "维护窗口不允许改路由"
    assert app_client.post(f"/api/change-requests/{request_id}/approve",
                           json={"actor": "主管", "note": "重新批准"}).status_code == 409


def test_approve_is_idempotent(app_client, broken_finding):
    device_id, result = broken_finding
    request_id = app_client.post(f"/api/devices/{device_id}/change-requests",
                                 json=create_payload([result["findings"][0]["id"]])).json()["data"]["id"]
    app_client.post(f"/api/change-requests/{request_id}/submit", json={"actor": "何深", "note": "提交"})
    body = {"actor": "主管", "note": "批准", "idempotency_key": "same-key-1"}
    first = app_client.post(f"/api/change-requests/{request_id}/approve", json=body)
    second = app_client.post(f"/api/change-requests/{request_id}/approve", json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["data"]["status"] == second.json()["data"]["status"] == "approved"
    events = app_client.get(f"/api/change-requests/{request_id}/events").json()["data"]
    assert [event["action"] for event in events].count("approve") == 1

    conflict = app_client.post(f"/api/change-requests/{request_id}/apply",
                               json={**body, "note": "换个动作复用同一个键"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_cross_device_and_non_violated_findings_are_rejected(app_client, broken_finding, r1_text):
    device_id, result = broken_finding
    other = app_client.post("/api/devices", json={"name": "r2"}).json()["data"]["id"]
    response = app_client.post(f"/api/devices/{other}/change-requests",
                               json=create_payload([result["findings"][0]["id"]]))
    assert response.status_code == 422 and response.json()["error"]["code"] == "FINDING_MISMATCH"

    clean_id = app_client.post("/api/devices", json={"name": "r3"}).json()["data"]["id"]
    app_client.post(f"/api/devices/{clean_id}/configs", json={"text": r1_text, "source_name": "clean"})
    unde = app_client.post(f"/api/devices/{clean_id}/rules/check", json={}).json()["data"]
    assert unde["findings"] == []
    # Use an undetermined finding instead: unparsable OSPF line.
    text = r1_text + "\ninterface eth9\n ip ospf bfd\n!\n"
    app_client.post(f"/api/devices/{clean_id}/configs", json={"text": text, "source_name": "unparsable"})
    unde = app_client.post(f"/api/devices/{clean_id}/rules/check", json={}).json()["data"]
    undetermined = [item for item in unde["findings"] if item["status"] == "undetermined"]
    assert undetermined
    response = app_client.post(f"/api/devices/{clean_id}/change-requests",
                               json=create_payload([undetermined[0]["id"]]))
    assert response.status_code == 422 and response.json()["error"]["code"] == "FINDING_NOT_ACTIONABLE"


def test_body_actor_is_ignored_because_identity_comes_from_the_session(app_client, broken_finding):
    device_id, result = broken_finding
    created = app_client.post(f"/api/devices/{device_id}/change-requests",
                              json=create_payload([result["findings"][0]["id"]], actor="system"))
    assert created.status_code == 201
    assert created.json()["data"]["created_by"] == "何深"


@pytest.mark.parametrize("updates,code", [
    ({"proposed_commands": []}, "EMPTY_COMMANDS"),
    ({"proposed_commands": ["ok", "   "]}, "INVALID_COMMAND"),
    ({"proposed_commands": ["a" * 201]}, "INVALID_COMMAND"),
    ({"proposed_commands": ["interface eth2\x07"]}, "INVALID_COMMAND"),
    ({"proposed_commands": [f"line {n}" for n in range(51)]}, "TOO_MANY_COMMANDS"),
    ({"title": "短"}, "INVALID_TITLE"),
    ({"rationale": "太短"}, "INVALID_RATIONALE"),
    ({"finding_ids": []}, "NO_FINDINGS"),
])
def test_payload_validation(app_client, broken_finding, updates, code):
    device_id, result = broken_finding
    payload = create_payload([result["findings"][0]["id"]])
    payload.update(updates)
    response = app_client.post(f"/api/devices/{device_id}/change-requests", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_duplicate_findings_are_rejected(app_client, broken_finding):
    device_id, result = broken_finding
    finding_id = result["findings"][0]["id"]
    response = app_client.post(f"/api/devices/{device_id}/change-requests",
                               json=create_payload([finding_id, finding_id]))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DUPLICATE_FINDING"


def test_cancel_from_draft(app_client, broken_finding):
    device_id, result = broken_finding
    request_id = app_client.post(f"/api/devices/{device_id}/change-requests",
                                 json=create_payload([result["findings"][0]["id"]])).json()["data"]["id"]
    cancelled = app_client.post(f"/api/change-requests/{request_id}/cancel",
                                json={"actor": "何深", "note": "重复提单，取消"})
    assert cancelled.json()["data"]["status"] == "cancelled"
    assert app_client.post(f"/api/change-requests/{request_id}/submit",
                           json={"actor": "何深", "note": "再次提交"}).status_code == 409


def test_missing_change_request_is_404(app_client):
    assert app_client.get("/api/change-requests/missing").status_code == 404
    assert app_client.post("/api/change-requests/missing/submit",
                           json={"actor": "何深", "note": "x"}).status_code == 404

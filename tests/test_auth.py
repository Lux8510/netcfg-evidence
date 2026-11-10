"""Local accounts, sessions and the editor/approver split."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8")
BROKEN = BASELINE.replace(" ip ospf cost 10\n", "")


def seed_finding(client) -> tuple[str, dict]:
    device = client.post("/api/devices", json={"name": "r1"}).json()["data"]["id"]
    client.post(f"/api/devices/{device}/configs", json={"text": BROKEN, "source_name": "broken"})
    run = client.post(f"/api/devices/{device}/rules/check", json={}).json()["data"]
    return device, run


def test_unauthenticated_requests_are_rejected(anon_client):
    assert anon_client.get("/api/devices").status_code == 401
    assert anon_client.get("/api/auth/me").status_code == 401
    assert anon_client.get("/api/health").status_code == 200


def test_login_logout_and_me(app_client):
    me = app_client.get("/api/auth/me").json()["data"]
    assert me["username"] == "何深" and me["role"] == "admin"
    assert app_client.post("/api/auth/logout").status_code == 200
    assert app_client.get("/api/devices").status_code == 401


def test_wrong_password_is_rejected(api_app):
    from fastapi.testclient import TestClient

    app, _, _ = api_app
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"username": "何深", "password": "wrong-password"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_session_cookie_is_httponly(app_client):
    app_client.post("/api/auth/logout")
    response = app_client.post("/api/auth/login", json={"username": "主管", "password": "approver-password-1"})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_editor_cannot_approve_but_can_edit(editor_client):
    device, run = seed_finding(editor_client)
    request = editor_client.post(f"/api/devices/{device}/change-requests", json={
        "title": "补回 cost",
        "rationale": "R001 要求显式 cost，需要人工核对后修复。",
        "finding_ids": [run["findings"][0]["id"]],
        "proposed_commands": ["interface eth2", " ip ospf cost 10"],
        "actor": "编辑",
    }).json()["data"]
    editor_client.post(f"/api/change-requests/{request['id']}/submit", json={"note": "提交"})
    denied = editor_client.post(f"/api/change-requests/{request['id']}/approve", json={"note": "自己批自己"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ROLE_REQUIRED"


def test_approver_can_decide_but_cannot_edit(approver_client):
    # Approver cannot create devices or configs.
    assert approver_client.post("/api/devices", json={"name": "nope"}).status_code == 403
    # But can read and decide.
    assert approver_client.get("/api/devices").status_code == 200


def test_two_roles_complete_one_change(editor_client, approver_client):
    device, run = seed_finding(editor_client)
    request = editor_client.post(f"/api/devices/{device}/change-requests", json={
        "title": "补回 cost",
        "rationale": "R001 要求显式 cost，需要人工核对后修复。",
        "finding_ids": [run["findings"][0]["id"]],
        "proposed_commands": ["interface eth2", " ip ospf cost 10"],
        "actor": "编辑",
    }).json()["data"]
    editor_client.post(f"/api/change-requests/{request['id']}/submit", json={"note": "提交"})
    approved = approver_client.post(f"/api/change-requests/{request['id']}/approve", json={"note": "同意"})
    assert approved.status_code == 200
    assert approved.json()["data"]["approved_by"] == "主管"

    applied = approver_client.post(f"/api/change-requests/{request['id']}/apply", json={"note": "已执行"})
    assert applied.status_code == 200

    events = editor_client.get(f"/api/change-requests/{request['id']}/events").json()["data"]
    assert [(event["action"], event["actor"]) for event in events] == [
        ("created", "编辑"), ("submit", "编辑"), ("approve", "主管"), ("apply", "主管")]


def test_audit_endpoint_lists_change_request_events(app_client):
    device, run = seed_finding(app_client)
    request = app_client.post(f"/api/devices/{device}/change-requests", json={
        "title": "补回 cost",
        "rationale": "R001 要求显式 cost，需要人工核对后修复。",
        "finding_ids": [run["findings"][0]["id"]],
        "proposed_commands": ["interface eth2", " ip ospf cost 10"],
    }).json()["data"]
    app_client.post(f"/api/change-requests/{request['id']}/submit", json={"note": "提交"})

    events = app_client.get(f"/api/devices/{device}/audit").json()["data"]
    assert [event["action"] for event in events][:2] == ["submit", "created"]
    assert all(event["actor"] == "何深" for event in events)

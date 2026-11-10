"""Read-only SSH collection and before/after change verification, against a fake device."""

from __future__ import annotations

import pytest

from collectors.ssh import ALLOWED_COMMANDS, CollectionError, Target, collect
from tests.fake_ssh import BASELINE


def test_collector_reads_allowlisted_commands(fake_device):
    state, port = fake_device
    result = collect(Target(host="127.0.0.1", port=port, username="netops", password="secret", timeout=5))
    assert "hostname r1" in result["config_text"]
    assert [item["command"] for item in result["commands"]] == list(ALLOWED_COMMANDS)
    assert all(item["exit_code"] == 0 for item in result["commands"])
    assert state.commands == list(ALLOWED_COMMANDS)


def test_collector_refuses_commands_outside_the_allowlist(fake_device):
    _, port = fake_device
    with pytest.raises(CollectionError) as excinfo:
        collect(Target(host="127.0.0.1", port=port, username="netops", password="secret"),
                commands=("show version",))
    assert excinfo.value.code == "COMMAND_NOT_ALLOWED"


def test_collector_reports_authentication_failure(fake_device):
    _, port = fake_device
    with pytest.raises(CollectionError) as excinfo:
        collect(Target(host="127.0.0.1", port=port, username="netops", password="wrong", timeout=3))
    assert excinfo.value.code == "SSH_AUTH_FAILED"


def test_api_collect_and_change_verification(app_client, fake_device, monkeypatch):
    state, port = fake_device
    state.password = "s3cr3t-value"
    monkeypatch.setenv("NETCFG_TEST_SSH_PASSWORD", "s3cr3t-value")

    device = app_client.post("/api/devices", json={"name": "ssh-r1"}).json()["data"]["id"]
    configured = app_client.put(f"/api/devices/{device}/target", json={
        "host": "127.0.0.1", "port": port, "username": "netops",
        "credential_ref": "NETCFG_TEST_SSH_PASSWORD",
    })
    assert configured.status_code == 200, configured.text
    assert "s3cr3t-value" not in configured.text

    collected = app_client.post(f"/api/devices/{device}/collect", json={"label": "baseline"})
    assert collected.status_code == 201, collected.text
    event = collected.json()["data"]
    assert event["event"]["status"] == "success"
    assert event["version"]["source_name"].startswith("ssh:")
    assert event["version"]["parsed"]["hostname"] == "r1"

    # Operator changes a cost out of band, then we verify the window.
    started = app_client.post(f"/api/devices/{device}/change-runs",
                              json={"note": "变更窗口：调整 eth2 的 OSPF cost"})
    assert started.status_code == 201, started.text
    run_id = started.json()["data"]["id"]

    state.outputs["show running-config"] = BASELINE.replace(" ip ospf cost 10", " ip ospf cost 20")
    completed = app_client.post(f"/api/change-runs/{run_id}/complete", json={"note": "变更后采集并核对"})
    assert completed.status_code == 200, completed.text
    run = completed.json()["data"]
    assert run["status"] == "completed"
    assert "interface.eth2.ospf.cost" in {item["path"] for item in run["diff_items"]}
    assert run["findings_counts"]["rules_checked"] == 4

    history = app_client.get(f"/api/devices/{device}/change-runs").json()["data"]
    assert [item["id"] for item in history] == [run_id]


def test_target_required_and_disable_switch(app_client, fake_device, monkeypatch):
    device = app_client.post("/api/devices", json={"name": "ssh-none"}).json()["data"]["id"]
    missing = app_client.post(f"/api/devices/{device}/collect", json={"label": "x"})
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "TARGET_NOT_CONFIGURED"

    app_client.put(f"/api/devices/{device}/target", json={
        "host": "127.0.0.1", "port": 22, "username": "netops", "enabled": False})
    disabled = app_client.post(f"/api/devices/{device}/collect", json={"label": "x"})
    assert disabled.status_code == 409 and disabled.json()["error"]["code"] == "TARGET_DISABLED"


def test_collection_failure_is_recorded(app_client, fake_device, monkeypatch):
    state, port = fake_device
    monkeypatch.setenv("NETCFG_TEST_SSH_PASSWORD", "wrong")
    device = app_client.post("/api/devices", json={"name": "ssh-bad"}).json()["data"]["id"]
    app_client.put(f"/api/devices/{device}/target", json={
        "host": "127.0.0.1", "port": port, "username": "netops",
        "credential_ref": "NETCFG_TEST_SSH_PASSWORD"})
    response = app_client.post(f"/api/devices/{device}/collect", json={"label": "bad"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SSH_AUTH_FAILED"
    versions = app_client.get(f"/api/devices/{device}/configs").json()["data"]
    assert versions == []


def test_approver_cannot_collect(approver_client):
    device = approver_client.post("/api/devices", json={"name": "x"}).status_code
    assert device == 403

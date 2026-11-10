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


def test_target_repr_does_not_contain_password():
    target = Target(host="127.0.0.1", port=22, username="netops", password="SUPER-SECRET-VALUE")
    assert "SUPER-SECRET-VALUE" not in repr(target)


def test_client_is_closed_when_authentication_raises(monkeypatch):
    from collectors import ssh as module

    class FakeClient:
        closed = 0

        def load_system_host_keys(self, *args, **kwargs): pass

        def set_missing_host_key_policy(self, *args): pass

        def connect(self, **kwargs):
            raise module.paramiko.AuthenticationException("bad")

        def close(self):
            FakeClient.closed += 1

    monkeypatch.setattr(module.paramiko, "SSHClient", lambda: FakeClient())
    with pytest.raises(CollectionError) as excinfo:
        collect(Target(host="127.0.0.1", port=22, username="x", password="y"))
    assert excinfo.value.code == "SSH_AUTH_FAILED"
    assert FakeClient.closed == 1


def test_collection_has_a_total_timeout(fake_device):
    state, port = fake_device
    state.delay = 3.0
    with pytest.raises(CollectionError) as excinfo:
        collect(Target(host="127.0.0.1", port=port, username="netops", password="secret", timeout=1))
    assert excinfo.value.code == "SSH_TIMEOUT"


def test_pagination_markers_are_removed():
    from collectors.ssh import _clean
    text = "line one\n--More--\b\b\b\b\b\b\b\bline two\n"
    cleaned = _clean(text)
    assert "--More--" not in cleaned and "line two" in cleaned


def test_api_rejects_host_and_credential_outside_allowlist(app_client, monkeypatch):
    monkeypatch.setenv("NETCFG_SSH_ALLOWED_HOSTS", "10.0.0.1")
    monkeypatch.setenv("NETCFG_SSH_ALLOWED_CREDENTIAL_REFS", "NETCFG_SSH_PASSWORD")
    device = app_client.post("/api/devices", json={"name": "allow"}).json()["data"]["id"]
    denied_host = app_client.put(f"/api/devices/{device}/target", json={
        "host": "192.0.2.9", "port": 22, "username": "netops",
        "credential_ref": "NETCFG_SSH_PASSWORD"})
    assert denied_host.status_code == 422 and denied_host.json()["error"]["code"] == "TARGET_HOST_NOT_ALLOWED"
    denied_ref = app_client.put(f"/api/devices/{device}/target", json={
        "host": "10.0.0.1", "port": 22, "username": "netops",
        "credential_ref": "OTHER_PASSWORD"})
    assert denied_ref.status_code == 422 and denied_ref.json()["error"]["code"] == "TARGET_CREDENTIAL_NOT_ALLOWED"


def test_strict_host_key_defaults_to_true(monkeypatch):
    from collectors.ssh import target_from_env
    monkeypatch.delenv("NETCFG_SSH_STRICT_HOST_KEY", raising=False)
    assert target_from_env("h", 22, "u", None, None).strict_host_key is True
    monkeypatch.setenv("NETCFG_SSH_STRICT_HOST_KEY", "false")
    assert target_from_env("h", 22, "u", None, None).strict_host_key is False


def test_change_run_versions_are_immutable_and_device_bound(app_client, app_session, fake_device, monkeypatch):
    import pytest as _pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError
    state, port = fake_device
    state.password = "s3cr3t-value"
    monkeypatch.setenv("NETCFG_TEST_SSH_PASSWORD", "s3cr3t-value")
    monkeypatch.setenv("NETCFG_SSH_STRICT_HOST_KEY", "false")
    device = app_client.post("/api/devices", json={"name": "immutable"}).json()["data"]["id"]
    app_client.put(f"/api/devices/{device}/target", json={
        "host": "127.0.0.1", "port": port, "username": "netops",
        "credential_ref": "NETCFG_TEST_SSH_PASSWORD"})
    run = app_client.post(f"/api/devices/{device}/change-runs", json={"note": "变更窗口用于不可变测试"}).json()["data"]

    with app_session() as db:
        with _pytest.raises(DatabaseError):
            db.execute(text("UPDATE change_runs SET before_version_id = 'other' WHERE id = :id"), {"id": run["id"]})
            db.commit()
        db.rollback()


def test_change_run_target_change_blocks_completion(app_client, fake_device, monkeypatch):
    state, port = fake_device
    state.password = "s3cr3t-value"
    monkeypatch.setenv("NETCFG_TEST_SSH_PASSWORD", "s3cr3t-value")
    monkeypatch.setenv("NETCFG_SSH_STRICT_HOST_KEY", "false")
    device = app_client.post("/api/devices", json={"name": "target-change"}).json()["data"]["id"]
    app_client.put(f"/api/devices/{device}/target", json={
        "host": "127.0.0.1", "port": port, "username": "netops",
        "credential_ref": "NETCFG_TEST_SSH_PASSWORD"})
    run = app_client.post(f"/api/devices/{device}/change-runs", json={"note": "变更窗口用于目标变化测试"}).json()["data"]
    app_client.put(f"/api/devices/{device}/target", json={
        "host": "127.0.0.1", "port": port, "username": "other",
        "credential_ref": "NETCFG_TEST_SSH_PASSWORD"})
    blocked = app_client.post(f"/api/change-runs/{run['id']}/complete", json={"note": "核对"})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "TARGET_CHANGED"


def test_completion_failure_is_persisted(app_client, app_session, fake_device, monkeypatch):
    state, port = fake_device
    state.password = "s3cr3t-value"
    monkeypatch.setenv("NETCFG_TEST_SSH_PASSWORD", "s3cr3t-value")
    monkeypatch.setenv("NETCFG_SSH_STRICT_HOST_KEY", "false")
    device = app_client.post("/api/devices", json={"name": "fail-persist"}).json()["data"]["id"]
    app_client.put(f"/api/devices/{device}/target", json={
        "host": "127.0.0.1", "port": port, "username": "netops",
        "credential_ref": "NETCFG_TEST_SSH_PASSWORD"})
    run = app_client.post(f"/api/devices/{device}/change-runs", json={"note": "变更窗口用于失败持久化测试"}).json()["data"]
    state.password = "changed-password"
    failed = app_client.post(f"/api/change-runs/{run['id']}/complete", json={"note": "核对"})
    assert failed.status_code == 502

    from engine.models import ChangeRun, CollectionEvent
    from sqlalchemy import func, select
    with app_session() as db:
        stored = db.get(ChangeRun, run["id"])
        assert stored.status == "failed" and stored.error_code == "SSH_AUTH_FAILED"
        events = db.scalar(select(func.count(CollectionEvent.id)))
        assert events == 2  # before success + after failure
        versions = db.scalar(select(func.count()).select_from(CollectionEvent).where(CollectionEvent.status == "failed"))
        assert versions == 1


def test_concurrent_completion_only_one_after_result(app_client, app_session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from domain.netcfg import collection

    device = app_client.post("/api/devices", json={"name": "concurrent-complete"}).json()["data"]["id"]
    monkeypatch.setenv("NETCFG_SSH_STRICT_HOST_KEY", "false")
    app_client.put(f"/api/devices/{device}/target", json={
        "host": "127.0.0.1", "port": 22, "username": "netops"})

    config_after = BASELINE.replace(" ip ospf cost 10", " ip ospf cost 20")

    def stub(target):
        return {"config_text": config_after,
                "commands": [{"command": "show running-config", "exit_code": 0,
                              "stdout": config_after, "stderr": ""}]}

    with app_session() as db:
        run = collection.start_change_run(db, device, "并发完成测试用的变更窗口说明", "何深", collector=stub)
        db.commit()
        run_id = run.id

    def worker():
        try:
            with app_session() as db:
                result = collection.complete_change_run(db, run_id, "核对", collector=stub)
                db.commit()
                return ("ok", result.after_version_id)
        except Exception as exc:  # noqa: BLE001
            return ("error", getattr(exc, "code", type(exc).__name__))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    with app_session() as db:
        stored = db.get(collection.ChangeRun, run_id)
        assert stored.status == "completed"
        assert stored.after_version_id
        after_ids = {value for status, value in results if status == "ok"}
        assert after_ids <= {stored.after_version_id}

"""Real parallel HTTP requests against a real server process (SQLite in WAL mode)."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8")
pytestmark = pytest.mark.concurrency


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def server(tmp_path):
    port = free_port()
    env = {"NETCFG_DATABASE_URL": f"sqlite:///{tmp_path / 'concurrent.db'}", "PATH": "/usr/bin:/bin",
           "HOME": str(tmp_path), "PYTHONPATH": str(ROOT), "PYTHONNOUSERSITE": "1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env={**__import__("os").environ, **env},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=0.5) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        process.kill()
        raise RuntimeError("server did not start")
    yield base
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def client() -> httpx.Client:
    return httpx.Client(timeout=20)


def create_device(base: str, name: str) -> str:
    with client() as http:
        response = http.post(base + "/api/devices", json={"name": name})
        assert response.status_code == 201, response.text
        return response.json()["data"]["id"]


def import_config(base: str, device_id: str, text: str, source: str) -> httpx.Response:
    with client() as http:
        return http.post(base + f"/api/devices/{device_id}/configs",
                         json={"text": text, "source_name": source})


def test_concurrent_identical_imports_keep_one_version(server):
    device_id = create_device(server, "r1")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: import_config(server, device_id, BASELINE, "same"), range(8)))
    assert {response.status_code for response in results} == {201}
    ids = {response.json()["data"]["id"] for response in results}
    assert len(ids) == 1
    with client() as http:
        versions = http.get(server + f"/api/devices/{device_id}/configs").json()["data"]
    assert len(versions) == 1


def test_concurrent_baseline_switch_keeps_one_active(server):
    device_id = create_device(server, "r1")
    first = import_config(server, device_id, BASELINE, "a").json()["data"]["id"]
    second = import_config(server, device_id, BASELINE.replace("hostname r1", "hostname r1b"), "b").json()["data"]["id"]

    def activate(version_id: str) -> int:
        with client() as http:
            return http.post(server + f"/api/devices/{device_id}/baseline",
                             json={"version_id": version_id}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(activate, [first, second]))
    assert set(statuses) <= {201, 409}
    assert 500 not in statuses
    with client() as http:
        active = http.get(server + f"/api/devices/{device_id}/baseline").json()["data"]
    assert active is not None and active["config_version_id"] in {first, second}


def test_concurrent_approve_and_reject_leave_one_decision(server):
    device_id = create_device(server, "r1")
    import_config(server, device_id, BASELINE.replace(" ip ospf cost 10\n", ""), "broken")
    with client() as http:
        run = http.post(server + f"/api/devices/{device_id}/rules/check", json={}).json()["data"]
        request = http.post(server + f"/api/devices/{device_id}/change-requests", json={
            "title": "补回 cost",
            "rationale": "R001 要求显式 cost，需要人工核对后修复。",
            "finding_ids": [run["findings"][0]["id"]],
            "proposed_commands": ["interface eth2", " ip ospf cost 10"],
            "actor": "何深",
        }).json()["data"]
        http.post(server + f"/api/change-requests/{request['id']}/submit",
                  json={"actor": "何深", "note": "提交"})

    def decide(action: str) -> int:
        with client() as http:
            return http.post(server + f"/api/change-requests/{request['id']}/{action}",
                             json={"actor": "主管", "note": action, "idempotency_key": f"{action}-race"}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(decide, ["approve", "reject"]))
    assert statuses == [200, 409], statuses

    with client() as http:
        state = http.get(server + f"/api/change-requests/{request['id']}").json()["data"]
        events = http.get(server + f"/api/change-requests/{request['id']}/events").json()["data"]
    assert state["status"] in {"approved", "rejected"}
    decisions = [event["action"] for event in events if event["action"] in {"approve", "reject"}]
    assert len(decisions) == 1

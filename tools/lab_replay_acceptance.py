#!/usr/bin/env python3
"""Offline replay of the FRR lab acceptance, using saved NetSecOps traces.

This is the replay half of the controlled-lab run. It starts a real netcfg
server on a temporary SQLite database, registers the lab environment/topology
and device mappings, exports bundles with the real NetSecOps handoff CLI, and
imports them through the authenticated endpoint. It then exercises duplicate,
wrong-context and human-approval paths.

The live FRR collection half needs Linux/WSL2 + Docker + Containerlab; see
docs/LAB_RUNBOOK.md for the commands and the recorded blocker.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import httpx

ROOT = Path(__file__).resolve().parents[1]
ADMIN = "lab-admin"
APPROVER = "lab-approver"
ADMIN_PASSWORD = "lab-admin-pass-1"
APPROVER_PASSWORD = "lab-approver-pass-1"
ENV = "lab"
TOPO = "ospf"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    done = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})},
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(cmd)}\n{done.stdout}\n{done.stderr}")
    return done


def api(client: httpx.Client, method: str, path: str, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise AssertionError(f"{method} {path} -> {response.status_code} {response.text}")
    return response.json().get("data")


def login(base: str, username: str, password: str) -> httpx.Client:
    client = httpx.Client(base_url=base, timeout=30)
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def export(netsecops: Path, trace: Path, device: str, out: Path) -> dict:
    script = netsecops / "labs/ospf-baseline/handoff.py"
    run([sys.executable, str(script), "--trace", str(trace), "--device", device,
         "--target", device, "--out", str(out)], cwd=script.parent)
    return json.loads(out.read_text(encoding="utf-8"))


def import_bundle(client: httpx.Client, device_id: str, bundle: dict,
                  environment: str = ENV, topology: str = TOPO) -> dict:
    return api(client, "POST", f"/api/devices/{device_id}/diagnosis-imports",
               json={"bundle": bundle, "environment": environment, "topology": topology})


def counts(client: httpx.Client, device_id: str) -> dict:
    return {
        "configs": len(api(client, "GET", f"/api/devices/{device_id}/configs",
                           params={"include_historical": True})),
        "rule_runs": len(api(client, "GET", f"/api/devices/{device_id}/rules/runs")),
        "findings": len(api(client, "GET", f"/api/devices/{device_id}/findings")),
        "change_requests": len(api(client, "GET", f"/api/devices/{device_id}/change-requests")),
        "imports": len(api(client, "GET", f"/api/devices/{device_id}/diagnosis-imports")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netsecops", type=Path,
                        default=Path(os.environ.get("NETSECOPS_DIR", "../netsecops-agent")))
    parser.add_argument("--out", type=Path,
                        default=ROOT / "docs/lab-runs/offline-replay.md")
    args = parser.parse_args(argv)
    netsecops = args.netsecops.resolve()
    fixtures = netsecops / "labs/ospf-baseline/tests/fixtures"
    if not (fixtures / "v04-failures/normal-trace.json").is_file():
        print(f"NetSecOps fixtures not found under {netsecops}", file=sys.stderr)
        return 2

    report = {"mode": "offline-replay", "environment": ENV, "topology": TOPO,
              "netcfg_commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                              capture_output=True, text=True, check=True).stdout.strip(),
              "netsecops_commit": subprocess.run(["git", "-C", str(netsecops), "rev-parse", "HEAD"],
                                                 capture_output=True, text=True, check=True).stdout.strip(),
              "cases": []}
    with tempfile.TemporaryDirectory(prefix="netcfg-lab-") as folder:
        temp = Path(folder)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "NETCFG_DATABASE_URL": f"sqlite:///{temp / 'lab.db'}",
               "EXPLAIN_PROVIDER": "mock", "NETCFG_COOKIE_SECURE": "false"}
        for username, password, role in ((ADMIN, ADMIN_PASSWORD, "admin"),
                                         (APPROVER, APPROVER_PASSWORD, "approver")):
            run([sys.executable, "scripts/create_user.py", "--username", username, "--role", role],
                cwd=ROOT, env={**env, "NETCFG_USER_PASSWORD": password})
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port),
             "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(base + "/api/health", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.2)
            else:
                raise RuntimeError("netcfg server did not start")

            admin = login(base, ADMIN, ADMIN_PASSWORD)
            approver = login(base, APPROVER, APPROVER_PASSWORD)
            r1 = api(admin, "POST", "/api/devices", json={"name": "r1"})["id"]
            r2 = api(admin, "POST", "/api/devices", json={"name": "r2"})["id"]
            for node, device_id in (("r1", r1), ("r2", r2)):
                api(admin, "POST", "/api/mappings", json={
                    "environment": ENV, "topology": TOPO, "node": node, "device_id": device_id})
            api(admin, "POST", "/api/links", json={
                "device_a_id": r1, "iface_a": "eth1", "device_b_id": r2, "iface_b": "eth1"})

            # 1) normal trace: evidence only, then traceability checks
            normal_trace = fixtures / "v04-failures/normal-trace.json"
            normal_bundle = export(netsecops, normal_trace, "r1", temp / "normal.json")
            normal = import_bundle(admin, r1, normal_bundle)
            assert normal["status"] == "no_draft" and normal["change_request_id"] is None
            detail = api(admin, "GET", f"/api/diagnosis-imports/{normal['id']}")
            assert detail["submitted_by"] == ADMIN
            assert detail["environment"] == ENV and detail["topology"] == TOPO
            assert detail["config_version_id"] and detail["rule_run_id"]
            findings = api(admin, "GET", f"/api/devices/{r1}/findings",
                           params={"run_id": detail["rule_run_id"]})
            assert all(item["config_version_id"] == detail["config_version_id"] for item in findings)
            version = api(admin, "GET", f"/api/versions/{detail['config_version_id']}")
            assert "hostname r1" in version["text"]
            assert detail["evidence"] and detail["evidence"][0].get("observations")
            assert detail["evidence"][0]["observations"][0].get("stdout")
            report["cases"].append({"case": "normal_replay", "status": normal["status"],
                                    "submitted_by": detail["submitted_by"],
                                    "config_version_id": detail["config_version_id"],
                                    "rule_run_id": detail["rule_run_id"],
                                    "findings": len(findings)})

            # 2) area mismatch: local R005 draft, submit + approve, then replay
            area_trace = fixtures / "traces/stage5-case-01.json"
            area_bundle = export(netsecops, area_trace, "r2", temp / "area.json")
            area = import_bundle(admin, r2, area_bundle)
            assert area["status"] == "draft"
            finding_rows = api(admin, "GET", f"/api/devices/{r2}/findings",
                               params={"run_id": area["rule_run_id"]})
            finding = next(row for row in finding_rows if row["id"] == area["finding_id"])
            assert finding["rule_id"] == "R005" and finding["status"] == "violated"
            change = api(admin, "GET", f"/api/change-requests/{area['change_request_id']}")
            assert change["status"] == "draft" and change["finding_ids"] == [finding["id"]]
            api(admin, "POST", f"/api/change-requests/{change['id']}/submit",
                json={"note": "lab replay: submit", "idempotency_key": "lab-submit-key"})
            approved = api(approver, "POST", f"/api/change-requests/{change['id']}/approve",
                           json={"note": "lab replay: approve", "idempotency_key": "lab-approve-key"})
            assert approved["status"] == "approved" and approved["approved_by"] == APPROVER
            replay = import_bundle(admin, r2, area_bundle)
            assert replay["duplicate"] is True and replay["id"] == area["id"]
            report["cases"].append({"case": "area_mismatch_replay", "status": area["status"],
                                    "finding_rule": finding["rule_id"],
                                    "change_status": approved["status"],
                                    "approved_by": approved["approved_by"],
                                    "duplicate": replay["duplicate"]})

            # 3) wrong context / wrong device: rejected with zero writes.
            # Check the request target, the mapped target and the possible peer.
            other = api(admin, "POST", "/api/devices", json={"name": "r2-other"})["id"]
            api(admin, "POST", "/api/mappings", json={
                "environment": "other", "topology": TOPO, "node": "r2", "device_id": other})
            targets = {"r2": r2, "other": other, "r1": r1}
            before = {name: counts(admin, device_id) for name, device_id in targets.items()}
            rejected_env = admin.post(f"/api/devices/{r1}/diagnosis-imports", json={
                "bundle": normal_bundle, "environment": "wrong-env", "topology": TOPO})
            assert rejected_env.status_code == 422
            assert rejected_env.json()["error"]["code"] == "MAPPING_NOT_FOUND"
            assert {name: counts(admin, device_id) for name, device_id in targets.items()} == before

            rejected_device = admin.post(f"/api/devices/{r2}/diagnosis-imports", json={
                "bundle": area_bundle, "environment": "other", "topology": TOPO})
            assert rejected_device.status_code == 422
            assert rejected_device.json()["error"]["code"] == "MAPPING_DEVICE_MISMATCH"
            assert {name: counts(admin, device_id) for name, device_id in targets.items()} == before

            report["cases"].append({"case": "wrong_context_rejected",
                                    "rights_unchanged": True,
                                    "wrong_device_unchanged": True})
            report["status"] = "PASS"
            admin.close()
            approver.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# 离线回放验收 · {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "",
             f"- 模式：`{report['mode']}`",
             f"- netcfg：`{report['netcfg_commit']}`",
             f"- netsecops：`{report['netsecops_commit']}`",
             f"- 环境/拓扑：`{ENV}/{TOPO}`", "", "| 场景 | 结果 |", "| --- | --- |"]
    for case in report["cases"]:
        lines.append(f"| {case['case']} | " + ", ".join(
            f"{key}={value}" for key, value in case.items() if key != "case") + " |")
    lines += ["", "> 这是保存轨迹的离线回放，不是现场 FRR 采集；现场采集需要 Linux/WSL2 + Docker + Containerlab。", ""]
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

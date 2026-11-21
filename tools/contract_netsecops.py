#!/usr/bin/env python3
"""Cross-repo contract test: NetSecOps handoff CLI -> netcfg import CLI.

Runs a real netcfg uvicorn process on a temporary SQLite database, exports
bundles with the NetSecOps CLI from saved traces, and imports them with the
netcfg CLI. Covers normal, area-mismatch, insufficient-evidence and a rejected
(tampered) bundle. Records both repository commits and the bundle schema in a
JSON report.

Usage:
    python tools/contract_netsecops.py --netsecops ../netsecops-agent

The NetSecOps checkout can be supplied with --netsecops or NETSECOPS_DIR.
Requires both CLIs to be runnable with the current Python interpreter.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

import httpx

ROOT = Path(__file__).resolve().parents[1]
USER = "contract-admin"
PASSWORD = "contract-password-1"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def git_head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None,
        expect: int | None = 0) -> subprocess.CompletedProcess:
    done = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})},
                          capture_output=True, text=True)
    if expect is not None and done.returncode != expect:
        raise AssertionError(f"command failed ({done.returncode}): {' '.join(cmd)}\n"
                             f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}")
    return done


def wait_health(base: str, process: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    process.kill()
    raise RuntimeError("netcfg server did not start")


def api(client: httpx.Client, method: str, path: str, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise AssertionError(f"{method} {path} -> {response.status_code} {response.text}")
    return response.json().get("data")


def get_or_create_device(client: httpx.Client, name: str) -> str:
    for row in api(client, "GET", "/api/devices"):
        if row["name"] == name:
            return row["id"]
    return api(client, "POST", "/api/devices", json={"name": name})["id"]


def export_bundle(netsecops: Path, trace: Path, device: str, target: str, out: Path) -> dict:
    handoff = netsecops / "labs/ospf-baseline/handoff.py"
    run([sys.executable, str(handoff), "--trace", str(trace), "--device", device,
         "--target", target, "--out", str(out)], cwd=handoff.parent)
    return json.loads(out.read_text(encoding="utf-8"))


def counts(client: httpx.Client, device_id: str) -> dict[str, int]:
    return {
        "configs": len(api(client, "GET", f"/api/devices/{device_id}/configs",
                           params={"include_historical": True})),
        "rule_runs": len(api(client, "GET", f"/api/devices/{device_id}/rules/runs")),
        "findings": len(api(client, "GET", f"/api/devices/{device_id}/findings")),
        "change_requests": len(api(client, "GET", f"/api/devices/{device_id}/change-requests")),
        "imports": len(api(client, "GET", f"/api/devices/{device_id}/diagnosis-imports")),
    }


def import_bundle(base: str, bundle: Path, device_id: str) -> tuple[int, dict | None]:
    cli = ROOT / "scripts/import_diagnosis.py"
    done = run([sys.executable, str(cli), "--bundle", str(bundle), "--base-url", base,
                "--username", USER, "--device-id", device_id],
               cwd=ROOT, env={"NETCFG_PASSWORD": PASSWORD}, expect=None)
    payload = None
    if done.returncode == 0:
        payload = json.loads(done.stdout)
    return done.returncode, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netsecops", type=Path,
                        default=Path(os.environ.get("NETSECOPS_DIR", "../netsecops-agent")))
    parser.add_argument("--out", type=Path, default=ROOT / "contract-report.json")
    args = parser.parse_args(argv)
    netsecops = args.netsecops.resolve()
    if not (netsecops / "labs/ospf-baseline/handoff.py").is_file():
        print(f"NetSecOps checkout not found: {netsecops}", file=sys.stderr)
        return 2
    fixtures = netsecops / "labs/ospf-baseline/tests/fixtures"

    report: dict = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "netcfg_commit": git_head(ROOT),
        "netsecops_commit": git_head(netsecops),
        "cases": [],
    }
    with tempfile.TemporaryDirectory(prefix="netcfg-contract-") as folder:
        temp = Path(folder)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "NETCFG_DATABASE_URL": f"sqlite:///{temp / 'contract.db'}",
               "EXPLAIN_PROVIDER": "mock", "NETCFG_COOKIE_SECURE": "false"}
        run([sys.executable, "scripts/create_user.py", "--username", USER, "--role", "admin"],
            cwd=ROOT, env={**env, "NETCFG_USER_PASSWORD": PASSWORD})
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port), "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            wait_health(base, server)
            with httpx.Client(base_url=base, timeout=30) as client:
                login = client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
                assert login.status_code == 200, login.text

                # 1) normal: no draft, idempotent
                r1 = get_or_create_device(client, "r1")
                normal_bundle = export_bundle(netsecops, fixtures / "v04-failures/normal-trace.json",
                                              "r1", "r1", temp / "normal.json")
                code, normal = import_bundle(base, temp / "normal.json", r1)
                assert code == 0 and normal["status"] == "no_draft", (code, normal)
                _, replay = import_bundle(base, temp / "normal.json", r1)
                assert replay["id"] == normal["id"] and replay["duplicate"] is True
                report["cases"].append({"case": "normal", "bundle_schema": normal_bundle["schema_version"],
                                        "status": normal["status"], "duplicate_ok": True})

                # 2) area mismatch: local R005 draft, idempotent
                r2 = get_or_create_device(client, "r2")
                link = client.post("/api/links", json={"device_a_id": r1, "iface_a": "eth1",
                                                       "device_b_id": r2, "iface_b": "eth1"})
                assert link.status_code in (201, 409), link.text
                area_bundle = export_bundle(netsecops, fixtures / "traces/stage5-case-01.json",
                                            "r2", "r2", temp / "area.json")
                code, area = import_bundle(base, temp / "area.json", r2)
                assert code == 0 and area["status"] == "draft", (code, area)
                findings = api(client, "GET", f"/api/devices/{r2}/findings",
                               params={"run_id": area["rule_run_id"]})
                finding = next((row for row in findings if row["id"] == area["finding_id"]), None)
                assert finding is not None, "linked finding is missing from the imported run"
                assert finding["rule_id"] == "R005" and finding["status"] == "violated"
                assert finding["config_version_id"] == area["config_version_id"]
                change = api(client, "GET", f"/api/change-requests/{area['change_request_id']}")
                assert change["status"] == "draft"
                assert change["finding_ids"] == [finding["id"]]
                _, area_replay = import_bundle(base, temp / "area.json", r2)
                assert area_replay["id"] == area["id"] and area_replay["duplicate"] is True
                report["cases"].append({"case": "area_mismatch", "bundle_schema": area_bundle["schema_version"],
                                        "status": area["status"], "finding_rule": finding["rule_id"],
                                        "finding_status": finding["status"],
                                        "config_version_id": area["config_version_id"],
                                        "change_request_id": area["change_request_id"]})

                # 3) insufficient evidence: evidence only
                insufficient_bundle = export_bundle(
                    netsecops, fixtures / "v04-failures/failed-collection-trace.json",
                    "r1", "r1", temp / "insufficient.json")
                code, insufficient = import_bundle(base, temp / "insufficient.json", r1)
                assert code == 0 and insufficient["status"] == "no_draft", (code, insufficient)
                report["cases"].append({"case": "insufficient_evidence",
                                        "bundle_schema": insufficient_bundle["schema_version"],
                                        "status": insufficient["status"]})

                # 4) rejection: tampered snapshot without rehashing
                before_counts = {name: counts(client, device_id) for name, device_id in (("r2", r2), ("r1", r1))}
                tampered = json.loads((temp / "area.json").read_text(encoding="utf-8"))
                tampered["config_snapshot"]["text"] += "\n!"
                (temp / "tampered.json").write_text(json.dumps(tampered), encoding="utf-8")
                code, _ = import_bundle(base, temp / "tampered.json", r2)
                assert code == 2, f"tampered bundle should exit 2, got {code}"
                after_counts = {name: counts(client, device_id) for name, device_id in (("r2", r2), ("r1", r1))}
                assert after_counts == before_counts, (before_counts, after_counts)
                report["cases"].append({"case": "rejected_tampered_bundle", "exit_code": code,
                                        "counts_unchanged": after_counts == before_counts})

                # 5) failure injected after the draft is written -> full rollback, retry succeeds
                failed_trace = temp / "area-retry-trace.json"
                trace = json.loads((fixtures / "traces/stage5-case-01.json").read_text(encoding="utf-8"))
                trace["task"] = str(trace.get("task")) + " [retry variant]"
                failed_trace.write_text(json.dumps(trace), encoding="utf-8")
                failed_bundle_path = temp / "area-retry.json"
                export_bundle(netsecops, failed_trace, "r2", "r2", failed_bundle_path)
                before_counts = {name: counts(client, device_id) for name, device_id in (("r2", r2), ("r1", r1))}
                database = temp / "contract.db"
                with sqlite3.connect(database) as conn:
                    conn.execute("CREATE TRIGGER contract_fail_import BEFORE INSERT ON diagnosis_imports "
                                 "BEGIN SELECT RAISE(ABORT, 'injected import failure'); END")
                    conn.commit()
                code, _ = import_bundle(base, failed_bundle_path, r2)
                assert code != 0, "injected failure was not reported"
                with sqlite3.connect(database) as conn:
                    conn.execute("DROP TRIGGER contract_fail_import")
                    conn.commit()
                after_counts = {name: counts(client, device_id) for name, device_id in (("r2", r2), ("r1", r1))}
                assert after_counts == before_counts, (before_counts, after_counts)
                code, retried = import_bundle(base, failed_bundle_path, r2)
                assert code == 0 and retried["status"] == "draft"
                report["cases"].append({"case": "rollback_after_draft_failure",
                                        "retry_exit_code": code,
                                        "counts_unchanged": after_counts == before_counts,
                                        "retry_status": retried["status"]})

                # 6) response lost after the server committed -> replay returns the original record
                lost_trace = temp / "area-lost-trace.json"
                trace = json.loads((fixtures / "traces/stage5-case-01.json").read_text(encoding="utf-8"))
                trace["task"] = str(trace.get("task")) + " [lost response variant]"
                lost_trace.write_text(json.dumps(trace), encoding="utf-8")
                lost_bundle_path = temp / "area-lost.json"
                export_bundle(netsecops, lost_trace, "r2", "r2", lost_bundle_path)
                bundle_payload = json.loads(lost_bundle_path.read_text(encoding="utf-8"))
                before_counts = counts(client, r2)

                class DropResponse(httpx.BaseTransport):
                    def __init__(self, inner: httpx.BaseTransport) -> None:
                        self.inner = inner
                    def handle_request(self, request: httpx.Request) -> httpx.Response:
                        self.inner.handle_request(request)
                        raise httpx.ReadError("simulated lost response")

                with httpx.Client(base_url=base, cookies=client.cookies,
                                  transport=DropResponse(httpx.HTTPTransport())) as dropping:
                    with __import__("pytest").raises(httpx.ReadError):
                        dropping.post(f"/api/devices/{r2}/diagnosis-imports", json={"bundle": bundle_payload})
                after_drop = api(client, "GET", f"/api/devices/{r2}/diagnosis-imports")
                first = next(row for row in after_drop if row["bundle_sha256"] == bundle_payload["bundle_sha256"])
                code, replay = import_bundle(base, lost_bundle_path, r2)
                assert code == 0 and replay["id"] == first["id"] and replay["duplicate"] is True
                assert replay["config_version_id"] == first["config_version_id"]
                assert replay["rule_run_id"] == first["rule_run_id"]
                assert replay["change_request_id"] == first["change_request_id"]
                assert counts(client, r2) == {**before_counts, "imports": before_counts["imports"] + 1,
                                              "rule_runs": before_counts["rule_runs"] + 1,
                                              "findings": before_counts["findings"] + len(
                                                  api(client, "GET", f"/api/devices/{r2}/findings",
                                                      params={"run_id": replay["rule_run_id"]})),
                                              "change_requests": before_counts["change_requests"] + 1}
                report["cases"].append({"case": "lost_response_replay", "duplicate": replay["duplicate"],
                                        "same_import": replay["id"] == first["id"]})

            report["status"] = "PASS"
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""A short load check: read-heavy traffic plus a few concurrent imports."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from scripts.load import run

ROOT = Path(__file__).resolve().parents[1]
USER = "压测"
PASSWORD = "load-password-1"
pytestmark = pytest.mark.load


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def server(tmp_path):
    port = free_port()
    env = {"NETCFG_DATABASE_URL": f"sqlite:///{tmp_path / 'load.db'}",
           "PYTHONPATH": str(ROOT), "PYTHONNOUSERSITE": "1"}
    subprocess.run([sys.executable, "scripts/create_user.py", "--username", USER, "--role", "admin"],
                   cwd=ROOT, env={**__import__("os").environ, **env, "NETCFG_USER_PASSWORD": PASSWORD},
                   check=True, capture_output=True, text=True)
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env={**__import__("os").environ, **env},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=0.5) as http:
                if http.get(base + "/api/health").status_code == 200:
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


def test_mixed_load_has_no_errors(server):
    report = run(server, requests=150, concurrency=15, username=USER, password=PASSWORD)
    assert report["errors"] == 0, report["error_samples"]
    assert report["latency_ms"]["p95"] < 2000, report["latency_ms"]
    assert report["throughput_rps"] > 10, report

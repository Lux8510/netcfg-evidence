"""Browser end-to-end tests against a real server and the built UI."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
E2E_USER = "何深"
E2E_PASSWORD = "e2e-password-1"
BASELINE = (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8")
BROKEN = BASELINE.replace(" ip ospf cost 10\n", "").replace(" passive-interface lo\n", "")
CHANGED = BASELINE.replace(" ip ospf cost 10", " ip ospf cost 30")
SECOND = (ROOT / "samples/frr/r2.conf").read_text(encoding="utf-8")

pytestmark = pytest.mark.e2e


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def server(tmp_path):
    port = free_port()
    env = {**os.environ, "NETCFG_DATABASE_URL": f"sqlite:///{tmp_path / 'e2e.db'}",
           "EXPLAIN_PROVIDER": "mock"}
    subprocess.run([sys.executable, "scripts/create_user.py", "--username", E2E_USER, "--role", "admin"],
                   cwd=ROOT, env={**env, "NETCFG_USER_PASSWORD": E2E_PASSWORD}, check=True,
                   capture_output=True, text=True)
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
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


@pytest.fixture()
def page(server):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"system Chrome unavailable: {exc}")
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        login = httpx.post(server + "/api/auth/login",
                           json={"username": E2E_USER, "password": E2E_PASSWORD})
        assert login.status_code == 200, login.text
        context.add_cookies([{"name": "netcfg_session",
                              "value": login.cookies["netcfg_session"], "url": server}])
        page = context.new_page()
        page.set_default_timeout(10000)
        yield page
        context.close()
        browser.close()


def api_login(base: str) -> httpx.Cookies:
    response = httpx.post(base + "/api/auth/login", json={"username": E2E_USER, "password": E2E_PASSWORD})
    assert response.status_code == 200, response.text
    return response.cookies


def api_post(base: str, path: str, payload: dict) -> dict:
    response = httpx.post(base + path, json=payload, cookies=api_login(base))
    assert response.status_code < 400, response.text
    return response.json()["data"]


def create_device(page, name: str) -> None:
    page.fill("[data-testid=device-name]", name)
    page.click("[data-testid=create-device]")
    page.wait_for_function("(name) => [...document.querySelectorAll('[data-testid=device-select] option')]"
                           ".some(option => option.textContent === name)", arg=name)
    page.select_option("[data-testid=device-select]", label=name)


def import_config(page, text: str, source: str) -> None:
    page.fill("[data-testid=source-name]", source)
    page.fill("[data-testid=config-text]", text)
    page.click("[data-testid=import-submit]")
    page.wait_for_selector(f"[data-testid=version-list] :text('{source}')")


def seed_drift(base: str, name: str = "drift-demo") -> str:
    device = api_post(base, "/api/devices", {"name": name})["id"]
    baseline = api_post(base, f"/api/devices/{device}/configs", {"text": BASELINE, "source_name": "baseline"})["id"]
    api_post(base, f"/api/devices/{device}/baseline", {"version_id": baseline})
    api_post(base, f"/api/devices/{device}/configs", {"text": CHANGED, "source_name": "changed"})
    api_post(base, f"/api/devices/{device}/drift", {})
    return device


def test_import_baseline_and_side_by_side_diff(page, server):
    page.goto(server)
    create_device(page, "r1-ui")
    import_config(page, BASELINE, "r1-baseline")
    page.click("[data-testid=version-list] [data-testid=set-baseline]")
    page.wait_for_selector("[data-testid=version-list] :text('基线')")

    import_config(page, CHANGED, "r1-changed")
    page.click("[data-testid=compute-drift]")
    page.wait_for_selector("[data-testid=drift-item]")
    page.click("[data-testid=drift-item] [data-testid=diff-button]")
    page.wait_for_selector("[data-testid=diff-body]")
    body = page.inner_text("[data-testid=diff-body]")
    assert "cost 10" in body and "cost 30" in body


def test_finding_to_approved_change_request(page, server):
    page.goto(server)
    create_device(page, "r1-change")
    import_config(page, BROKEN, "broken")
    page.click("[data-testid=run-rules]")
    page.wait_for_selector("[data-testid=finding]")
    assert "R001" in page.inner_text("[data-testid=rules-result]")

    page.click("[data-testid=generate-change]")
    page.wait_for_selector("[data-testid=cr-title]")
    page.click("[data-testid=cr-submit]")
    page.wait_for_selector("[data-testid=changes-list] :text('修复 R001')")

    page.click("[data-testid=transition-submit]")
    page.fill("[data-testid=transition-note]", "提交审批")
    page.click("[data-testid=transition-confirm]")
    page.wait_for_selector("[data-testid=transition-approve]")

    page.click("[data-testid=transition-approve]")
    page.fill("[data-testid=transition-note]", "同意")
    page.click("[data-testid=transition-confirm]")
    page.wait_for_selector("[data-testid=changes-list] :text('approved')")
    page.wait_for_selector("[data-testid=audit-list] :text('approve')")


def test_switching_device_clears_previous_findings(page, server):
    page.goto(server)
    create_device(page, "switch-a")
    import_config(page, BROKEN, "broken")
    page.click("[data-testid=run-rules]")
    page.wait_for_selector("[data-testid=finding]")

    create_device(page, "switch-b")
    page.wait_for_timeout(400)
    assert page.locator("[data-testid=finding]").count() == 0


def test_exemption_from_ui_and_revoke(page, server):
    device = seed_drift(server)
    page.goto(server)
    page.select_option("[data-testid=device-select]", device)
    page.wait_for_selector("[data-testid=drift-item]")

    page.click("[data-testid=exempt-button]")
    page.click("[data-testid=exempt-submit]")
    page.wait_for_selector("[data-testid=drift-item] :text('豁免至')")

    page.click("[data-testid=revoke-exemption]")
    page.fill("[data-testid=revoke-note]", "偏差已整改")
    page.click("text=确认撤销")
    page.wait_for_selector("[data-testid=exempt-button]")


def test_topology_and_audit(page, server):
    first = api_post(server, "/api/devices", {"name": "topo-a"})["id"]
    second = api_post(server, "/api/devices", {"name": "topo-b"})["id"]
    api_post(server, f"/api/devices/{first}/configs", {"text": BASELINE, "source_name": "a"})
    api_post(server, f"/api/devices/{second}/configs", {"text": SECOND, "source_name": "b"})
    api_post(server, "/api/links", {"device_a_id": first, "iface_a": "eth2", "device_b_id": second, "iface_b": "eth1"})

    page.goto(server)
    page.wait_for_selector("[data-testid=topo-svg]")
    assert "topo-a" in page.locator("[data-testid=topo-svg]").text_content()
    page.click("[data-testid=topo-node]:has-text('topo-b')")
    page.wait_for_function("() => document.querySelector('[data-testid=device-select]').selectedOptions[0].textContent === 'topo-b'")

    seed = api_post(server, "/api/devices", {"name": "audit-a"})["id"]
    api_post(server, f"/api/devices/{seed}/configs", {"text": BROKEN, "source_name": "b"})
    run = api_post(server, f"/api/devices/{seed}/rules/check", {})
    api_post(server, f"/api/devices/{seed}/change-requests", {
        "title": "审计用变更单", "rationale": "R001 要求显式 cost，需要人工核对后修复。",
        "finding_ids": [run["findings"][0]["id"]], "proposed_commands": ["interface eth2"]})
    page.goto(server)
    page.select_option("[data-testid=device-select]", seed)
    page.wait_for_selector("[data-testid=audit-list] :text('created')")


def test_user_content_is_escaped(page, server):
    payload = '<img src=x onerror="window.__xss=1">'
    device = api_post(server, "/api/devices", {"name": payload})["id"]
    api_post(server, f"/api/devices/{device}/configs", {"text": BROKEN, "source_name": payload})
    run = api_post(server, f"/api/devices/{device}/rules/check", {})
    api_post(server, f"/api/devices/{device}/change-requests", {
        "title": payload, "rationale": "R001 要求显式 cost，需要人工核对后修复。",
        "finding_ids": [run["findings"][0]["id"]], "proposed_commands": ["interface eth2"]})

    page.goto(server)
    page.select_option("[data-testid=device-select]", device)
    page.wait_for_selector("[data-testid=changes-list] article")
    assert page.evaluate("window.__xss === undefined")
    assert page.evaluate("document.querySelectorAll('[data-testid=changes-list] img').length") == 0
    assert payload in page.inner_text("[data-testid=changes-list]")


def test_explanations_are_generated_and_listed(page, server):
    page.goto(server)
    create_device(page, "explain-a")
    import_config(page, BROKEN, "broken")
    page.click("[data-testid=run-rules]")
    page.wait_for_selector("[data-testid=finding]")
    page.click("[data-testid=explain-run]")
    page.wait_for_selector("[data-testid=explain-list] article")
    text = page.inner_text("[data-testid=explain-list]")
    assert "R001" in text and "引用" in text

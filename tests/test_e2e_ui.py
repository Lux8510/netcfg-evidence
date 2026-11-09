from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.e2e

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def server(tmp_path):
    port = free_port()
    env = {**os.environ, "NETCFG_DATABASE_URL": f"sqlite:///{tmp_path / 'e2e.db'}"}
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
def page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # no system Chrome available
            pytest.skip(f"system Chrome unavailable: {exc}")
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(8000)
        yield page
        context.close()
        browser.close()


BASELINE = (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8")
BROKEN = BASELINE.replace(" ip ospf cost 10\n", "").replace(" passive-interface lo\n", "")
CHANGED = BASELINE.replace(" ip ospf cost 10", " ip ospf cost 30")


def create_device(page, base: str, name: str) -> None:
    page.goto(base)
    page.fill("#device-name", name)
    page.click("#create-device")
    page.wait_for_function("(name) => [...document.querySelectorAll('#device-select option')]"
                           ".some(option => option.textContent === name)", arg=name)


def import_config(page, text: str, source: str) -> None:
    page.fill("#source-name", source)
    page.fill("#config-text", text)
    page.click("#import-form button[type=submit]")
    page.wait_for_selector(f"#version-list article:has-text('{source}')")


def open_tab(page, name: str) -> None:
    page.click(f".tab[data-tab='{name}']")


def accept_dialog(page, text: str) -> None:
    page.once("dialog", lambda dialog: dialog.accept(text))


def test_import_set_baseline_and_inspect_drift_evidence(page, server):
    create_device(page, server, "r1")
    import_config(page, BASELINE, "r1-baseline")
    page.click("#version-list article:has-text('r1-baseline') button[data-baseline]")
    page.wait_for_selector("#baseline-label:has-text('r1-baseline')")

    import_config(page, CHANGED, "r1-after-change")
    page.click("#compute-drift")
    page.wait_for_selector("#drift-result .badge.changed")
    assert "interface.eth2.ospf.cost" in page.inner_text("#drift-result")

    page.click("#drift-result article:has-text('interface.eth2.ospf.cost') button:has-text('基线第')")
    page.wait_for_selector("#lines-dialog[open]")
    assert "cost 10" in page.inner_text("#lines-body")
    page.click("#lines-close")

    # The buttons must open different versions: baseline shows 10, current shows 30.
    page.click("#drift-result article:has-text('interface.eth2.ospf.cost') button:has-text('当前第')")
    page.wait_for_selector("#lines-dialog[open]")
    assert "cost 30" in page.inner_text("#lines-body")


def test_rule_finding_to_approved_change_request(page, server):
    create_device(page, server, "r1")
    import_config(page, BROKEN, "r1-broken")
    open_tab(page, "rules")
    page.click("#run-rules")
    page.wait_for_selector("#rules-result .badge.error")
    assert "R001" in page.inner_text("#rules-result")

    page.click("#rules-result button:has-text('生成变更单')")
    page.fill("#cr-title", "补回 eth2 的 cost")
    page.fill("#cr-rationale", "规则 R001 要求点到点接口显式设置 cost，需要人工核对后修复。")
    page.fill("#cr-commands", "interface eth2\n ip ospf cost 10")
    page.fill("#cr-actor", "何深")
    page.click("#cr-submit")
    page.wait_for_selector("#changes-result article:has-text('补回 eth2 的 cost')")
    accept_dialog(page, "提交审批")
    page.click("#changes-result article button:has-text('提交审批')")
    page.wait_for_selector("#changes-result article .badge:has-text('pending_approval')")

    page.evaluate("localStorage.setItem('actor', '主管')")
    accept_dialog(page, "同意")
    page.click("#changes-result article button:has-text('批准')")
    page.wait_for_selector("#changes-result article .badge:has-text('approved')")

    page.click("#changes-result article button:has-text('审计事件')")
    page.wait_for_selector("#changes-result .events:has-text('approve')")
    assert "created" in page.inner_text("#changes-result .events")


def test_switching_device_clears_previous_findings(page, server):
    create_device(page, server, "r1")
    import_config(page, BROKEN, "r1-broken")
    open_tab(page, "rules")
    page.click("#run-rules")
    page.wait_for_selector("#rules-result .badge.error")

    page.fill("#device-name", "r2")
    page.click("#create-device")
    page.wait_for_function("() => document.querySelector('#device-select').value !== ''")

    open_tab(page, "rules")
    assert "R001" not in page.inner_text("#rules-result")
    assert "还没有" in page.inner_text("#rules-result")
    assert page.is_hidden("#change-form")


def test_explanations_show_rejected_and_accepted_marks(page, server):
    create_device(page, server, "r1")
    import_config(page, BROKEN, "r1-broken")
    open_tab(page, "rules")
    page.click("#run-rules")
    page.wait_for_selector("#rules-result .badge.error")
    open_tab(page, "explain")
    page.click("#run-explain")
    page.wait_for_selector("#explain-result article")
    text = page.inner_text("#explain-result")
    assert "R001" in text and "引用" in text


import httpx  # noqa: E402


def api_post(base: str, path: str, payload: dict) -> dict:
    with httpx.Client(timeout=10) as http:
        response = http.post(base + path, json=payload)
        assert response.status_code < 400, response.text
        return response.json()["data"]


def seed_change_request(base: str, name: str, title: str) -> tuple[str, str]:
    device = api_post(base, "/api/devices", {"name": name})
    api_post(base, f"/api/devices/{device['id']}/configs", {"text": BROKEN, "source_name": "b"})
    run = api_post(base, f"/api/devices/{device['id']}/rules/check", {})
    request = api_post(base, f"/api/devices/{device['id']}/change-requests", {
        "title": title,
        "rationale": "R001 要求显式 cost，需要人工核对后修复。",
        "finding_ids": [run["findings"][0]["id"]],
        "proposed_commands": ["interface eth2", " ip ospf cost 10"],
        "actor": "何深",
    })
    return device["id"], request["id"]


def test_switching_device_discards_inflight_change_requests(page, server):
    first, request_id = seed_change_request(server, "race-a", "A 的变更单")
    second, _ = seed_change_request(server, "race-b", "B 的变更单")

    def slow(route):
        import time
        time.sleep(1.2)
        route.continue_()

    page.route(f"**/api/devices/{first}/change-requests", slow)
    page.goto(server)
    page.select_option("#device-select", first)
    page.wait_for_timeout(150)
    page.select_option("#device-select", second)

    page.wait_for_timeout(2500)
    page.click(".tab[data-tab='changes']")
    stale_buttons = page.evaluate(
        "(device) => document.querySelectorAll(`[data-device='${device}']`).length", first)
    assert stale_buttons == 0
    text = page.inner_text("#changes-result")
    assert "A 的变更单" not in text
    assert "B 的变更单" in text


def test_user_content_is_escaped_in_the_browser(page, server):
    payload_name = '<img src=x onerror="window.__xss=1">'
    title = '<img src=x onerror="window.__xss=2">'
    seed_change_request(server, payload_name, title)

    page.goto(server)
    page.click(".tab[data-tab='changes']")
    page.wait_for_selector("#changes-result article")
    assert page.evaluate("window.__xss === undefined")
    assert title in page.inner_text("#changes-result")
    assert page.evaluate("document.querySelectorAll('#changes-result img').length === 0")
    assert page.evaluate("document.querySelectorAll('#device-select img').length === 0")

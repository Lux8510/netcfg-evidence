"""Browser end-to-end tests against a real server and the built UI."""

from __future__ import annotations

import json
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
           "EXPLAIN_PROVIDER": "mock", "NETCFG_TEST_SSH_PASSWORD": "e2e-ssh-secret", "NETCFG_SSH_STRICT_HOST_KEY": "false"}
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


def test_delayed_response_from_previous_device_is_dropped(page, server):
    first = api_post(server, "/api/devices", {"name": "race-a"})["id"]
    second = api_post(server, "/api/devices", {"name": "race-b"})["id"]
    api_post(server, f"/api/devices/{first}/configs", {"text": BASELINE, "source_name": "A-version"})
    api_post(server, f"/api/devices/{second}/configs", {"text": SECOND, "source_name": "B-version"})

    def slow(route):
        import time
        time.sleep(1.4)
        route.continue_()

    page.route(f"**/api/devices/{first}/configs", slow)
    page.goto(server)
    page.select_option("[data-testid=device-select]", first)
    page.wait_for_timeout(150)
    page.select_option("[data-testid=device-select]", second)
    page.wait_for_selector("[data-testid=version-list] :text('B-version')")
    page.wait_for_timeout(1800)
    text = page.inner_text("[data-testid=version-list]")
    assert "B-version" in text and "A-version" not in text


def test_peer_evidence_opens_the_peer_version(page, server):
    first = api_post(server, "/api/devices", {"name": "peer-a"})["id"]
    second = api_post(server, "/api/devices", {"name": "peer-b"})["id"]
    api_post(server, f"/api/devices/{first}/configs", {"text": BASELINE, "source_name": "a"})
    wrong_area = SECOND.replace("network 10.12.0.0/30 area 0.0.0.0", "network 10.12.0.0/30 area 0.0.0.1")
    api_post(server, f"/api/devices/{second}/configs", {"text": wrong_area, "source_name": "b"})
    api_post(server, "/api/links", {"device_a_id": first, "iface_a": "eth2", "device_b_id": second, "iface_b": "eth1"})

    page.goto(server)
    page.select_option("[data-testid=device-select]", first)
    page.wait_for_selector("[data-testid=run-rules]")
    page.click("[data-testid=run-rules]")
    page.wait_for_selector("[data-testid=finding][data-rule='R005']")
    finding = page.locator("[data-testid=finding][data-rule='R005']")
    buttons = finding.locator("[data-testid=evidence-button]")
    assert buttons.count() >= 2
    buttons.nth(1).click()
    page.wait_for_function("() => document.querySelector('[data-testid=evidence-body]')?.textContent?.includes('0.0.0.1')")


def test_explanations_are_scoped_to_the_latest_run(page, server):
    device = api_post(server, "/api/devices", {"name": "scoped-explain"})["id"]
    api_post(server, f"/api/devices/{device}/configs", {"text": BROKEN, "source_name": "broken"})
    api_post(server, f"/api/devices/{device}/rules/check", {})
    api_post(server, f"/api/devices/{device}/explanations", {})
    # A newer clean run has no findings and therefore no explanations.
    api_post(server, f"/api/devices/{device}/configs", {"text": BASELINE, "source_name": "clean"})
    api_post(server, f"/api/devices/{device}/rules/check", {})

    page.goto(server)
    page.select_option("[data-testid=device-select]", device)
    page.wait_for_selector("[data-testid=explain-list]")
    page.wait_for_timeout(600)
    text = page.inner_text("[data-testid=explain-list]")
    assert "R001" not in text


def test_3d_background_turns_off_when_window_shrinks(page, server):
    page.set_viewport_size({"width": 1200, "height": 900})
    page.goto(server)
    page.wait_for_selector("canvas", timeout=10000)
    page.set_viewport_size({"width": 800, "height": 900})
    page.wait_for_function("() => document.querySelectorAll('canvas').length === 0", timeout=5000)


def test_dialog_focus_and_topology_keyboard(page, server):
    device = seed_drift(server)
    page.goto(server)
    page.select_option("[data-testid=device-select]", device)
    page.wait_for_selector("[data-testid=drift-item]")
    page.click("[data-testid=drift-item] [data-testid=diff-button]")
    page.wait_for_selector("[data-testid=diff-body]")
    inside = page.evaluate("() => !!document.querySelector('[role=dialog]')?.contains(document.activeElement)")
    assert inside
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.querySelector('[role=dialog]')")

    first = api_post(server, "/api/devices", {"name": "kb-a"})["id"]
    second = api_post(server, "/api/devices", {"name": "kb-b"})["id"]
    api_post(server, f"/api/devices/{first}/configs", {"text": BASELINE, "source_name": "a"})
    api_post(server, f"/api/devices/{second}/configs", {"text": SECOND, "source_name": "b"})
    api_post(server, "/api/links", {"device_a_id": first, "iface_a": "eth2", "device_b_id": second, "iface_b": "eth1"})
    page.goto(server)
    page.select_option("[data-testid=device-select]", first)
    page.wait_for_selector("[data-testid=topo-svg]")
    node = page.locator("[data-testid=topo-node][data-device='" + second + "']")
    node.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("(id) => document.querySelector('[data-testid=device-select]').value === id", arg=second)


def test_theme_toggle_persists(page, server):
    page.goto(server)
    page.wait_for_selector("[data-testid=theme-toggle]")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.click("[data-testid=theme-toggle]")
    page.wait_for_function("() => document.documentElement.dataset.theme === 'light'")
    page.reload()
    page.wait_for_selector("[data-testid=theme-toggle]")
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    page.click("[data-testid=theme-toggle]")
    page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")


def test_ssh_change_verification_flow(page, server, fake_device):
    state, ssh_port = fake_device
    state.password = "e2e-ssh-secret"

    page.goto(server)
    create_device(page, "ssh-ui")
    page.fill("[data-testid=target-host]", "127.0.0.1")
    page.fill("[data-testid=target-port]", str(ssh_port))
    page.fill("[data-testid=target-username]", "netops")
    page.fill("[data-testid=target-credential]", "NETCFG_TEST_SSH_PASSWORD")
    page.click("[data-testid=target-save]")
    page.wait_for_function("() => !document.querySelector('[data-testid=collect-now]').disabled")

    page.click("[data-testid=collect-now]")
    page.wait_for_selector("[data-testid=version-list] :text('ssh:manual')")

    page.click("[data-testid=start-change]")
    page.fill("[data-testid=change-note]", "变更窗口：调整 eth2 的 OSPF cost 后核对")
    page.click("[data-testid=change-submit]")
    page.wait_for_selector("[data-testid=change-runs] :text('open')")

    state.outputs["show running-config"] = BASELINE.replace(" ip ospf cost 10", " ip ospf cost 20")
    page.click("[data-testid=complete-change]")
    page.fill("[data-testid=change-note]", "变更后采集并核对差异")
    page.click("[data-testid=change-submit]")
    page.wait_for_selector("[data-testid=change-runs] :text('completed')")
    assert "interface.eth2.ospf.cost" in page.inner_text("[data-testid=change-runs]")


def test_switch_device_while_collecting_discards_old_response(page, server, fake_device):
    state, ssh_port = fake_device
    state.password = "e2e-ssh-secret"
    first = api_post(server, "/api/devices", {"name": "collect-a"})["id"]
    second = api_post(server, "/api/devices", {"name": "collect-b"})["id"]
    for device, host in ((first, "127.0.0.1"), (second, "localhost")):
        with httpx.Client(base_url=server) as http:
            login = http.post("/api/auth/login", json={"username": E2E_USER, "password": E2E_PASSWORD})
            response = http.put(f"/api/devices/{device}/target", cookies=login.cookies, json={
                "host": host, "port": ssh_port, "username": "netops",
                "credential_ref": "NETCFG_TEST_SSH_PASSWORD"})
            assert response.status_code == 200, response.text

    def slow(route):
        import time
        time.sleep(1.5)
        route.continue_()

    page.route(f"**/api/devices/{first}/collect", slow)
    page.goto(server)
    page.select_option("[data-testid=device-select]", first)
    page.wait_for_function("() => document.querySelector('[data-testid=target-host]').value === '127.0.0.1'")
    page.click("[data-testid=collect-now]")
    page.select_option("[data-testid=device-select]", second)
    page.wait_for_function("() => document.querySelector('[data-testid=target-host]').value === 'localhost'")
    page.wait_for_timeout(2000)
    assert page.input_value("[data-testid=target-host]") == "localhost"


def test_evidence_spine_links_drift_and_rule_evidence(page, server):
    first = api_post(server, "/api/devices", {"name": "spine-a"})["id"]
    second = api_post(server, "/api/devices", {"name": "spine-b"})["id"]
    baseline = api_post(server, f"/api/devices/{first}/configs", {"text": BASELINE, "source_name": "baseline"})["id"]
    api_post(server, f"/api/devices/{first}/baseline", {"version_id": baseline})
    api_post(server, f"/api/devices/{first}/configs", {"text": CHANGED, "source_name": "changed"})
    wrong_area = SECOND.replace("network 10.12.0.0/30 area 0.0.0.0", "network 10.12.0.0/30 area 0.0.0.1")
    api_post(server, f"/api/devices/{second}/configs", {"text": wrong_area, "source_name": "peer"})
    api_post(server, "/api/links", {"device_a_id": first, "iface_a": "eth2", "device_b_id": second, "iface_b": "eth1"})

    page.goto(server)
    page.select_option("[data-testid=device-select]", first)
    page.wait_for_selector("[data-testid=version-list] :text('changed')")
    page.click("[data-testid=compute-drift]")
    page.wait_for_selector("[data-testid=drift-item] [data-testid=spine-line]")
    page.click("[data-testid=run-rules]")
    page.wait_for_selector("[data-testid=finding][data-rule='R005']")
    page.wait_for_timeout(600)  # let the stagger animation settle before hovering
    check = api_post(server, f"/api/devices/{first}/rules/check", {"include_cross": True})
    r005 = next(item for item in check["findings"] if item["rule_id"] == "R005")
    eth2_line = next(item["line"] for item in r005["evidence"]
                     if (item.get("path") or "").startswith("interface.eth2"))

    # Hovering a rule evidence line highlights the drift item for the same interface.
    page.locator(f"[data-testid=finding][data-rule='R005'] [data-testid=spine-line][data-line='{eth2_line}']").hover()
    page.wait_for_timeout(200)
    assert page.locator("[data-testid=drift-item][class*='accent']").count() >= 1

    # Hovering the drift line highlights the R005 finding.
    page.locator("[data-testid=drift-item] [data-testid=spine-line]").first.hover()
    page.wait_for_timeout(200)
    assert page.locator("[data-testid=finding][data-rule='R005'][class*='accent']").count() == 1

    # Clicking the line opens the evidence dialog at that line.
    page.locator("[data-testid=drift-item] [data-testid=spine-line]").first.click()
    page.wait_for_function("() => document.querySelector('[data-testid=evidence-body]')"
                           "?.textContent?.includes('ip ospf cost 30')")


def _diagnosis_bundle(config: str, node: str = "r2") -> dict:
    import hashlib
    import json

    def canonical(payload):
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(config.encode("utf-8")).hexdigest()
    observation = {"node": node, "command": ["vtysh", "-c", "show running-config"],
                   "exit_code": 0, "stdout": config, "stderr": "",
                   "stdout_truncated": False, "stderr_truncated": False,
                   "stdout_bytes": len(config.encode()), "stderr_bytes": 0}
    citation_digest = hashlib.sha256(canonical([observation])).hexdigest()
    bundle = {
        "schema_version": 3, "source": "netsecops-agent", "trace_id": "e" * 64,
        "created_at_utc": "2026-11-20T00:00:00+00:00",
        "device": {"name": node},
        "diagnosis": {"status": "normal", "conclusion": "两端 Area 一致，连通正常。",
                       "repair_suggestion": None, "evidence_ids": ["E1"]},
        "config_snapshot": {"node": node, "target": node, "evidence_id": "E1",
                            "command": ["vtysh", "-c", "show running-config"], "exit_code": 0,
                            "text": config, "sha256": digest},
        "proposed_commands": [],
        "citations": [{"evidence_id": "E1", "tool": "query_interface_config",
                       "arguments": {"node": node, "interface": "eth1"}, "node": node,
                       "observations": [observation],
                       "content_sha256": citation_digest}],
        "link": {"node": node, "interface": "eth1", "peer": "r1", "peer_interface": "eth2"},
        "lab_only": True,
    }
    bundle["bundle_sha256"] = hashlib.sha256(canonical(bundle)).hexdigest()
    return bundle


def _area_diagnosis_bundle(local: str, peer: str) -> dict:
    import hashlib
    import json

    def canonical(payload):
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def obs(node, text):
        return {"node": node, "command": ["vtysh", "-c", "show running-config"], "exit_code": 0,
                "stdout": text, "stderr": "", "stdout_truncated": False, "stderr_truncated": False,
                "stdout_bytes": len(text.encode()), "stderr_bytes": 0}

    def citation(eid, node, text):
        observations = [obs(node, text)]
        return {"evidence_id": eid, "tool": "query_interface_config",
                "arguments": {"node": node, "interface": "eth1"}, "node": node,
                "observations": observations,
                "content_sha256": hashlib.sha256(canonical(observations)).hexdigest()}

    local_sha, peer_sha = hashlib.sha256(local.encode()).hexdigest(), hashlib.sha256(peer.encode()).hexdigest()
    bundle = {
        "schema_version": 3, "source": "netsecops-agent", "trace_id": "f" * 64,
        "created_at_utc": "2026-11-20T00:00:00+00:00",
        "device": {"name": "r2"},
        "diagnosis": {"status": "ospf_area_mismatch", "conclusion": "两端 Area 不一致。",
                       "repair_suggestion": None, "evidence_ids": ["E1"]},
        "config_snapshot": {"node": "r2", "target": "r2", "evidence_id": "E1",
                            "command": ["vtysh", "-c", "show running-config"], "exit_code": 0,
                            "text": local, "sha256": local_sha},
        "peer_snapshot": {"node": "r1", "interface": "eth2", "evidence_id": "PEER",
                          "command": ["vtysh", "-c", "show running-config"], "exit_code": 0,
                          "text": peer, "sha256": peer_sha},
        "proposed_commands": ["no ip ospf area 0.0.0.1"],
        "citations": [citation("E1", "r2", local), citation("PEER", "r1", peer)],
        "link": {"node": "r2", "interface": "eth1", "peer": "r1", "peer_interface": "eth2"},
        "lab_only": True,
    }
    bundle["bundle_sha256"] = hashlib.sha256(canonical(bundle)).hexdigest()
    return bundle


def test_diagnosis_import_opens_local_findings_and_evidence(page, server):
    r1 = api_post(server, "/api/devices", {"name": "r1"})["id"]
    r2 = api_post(server, "/api/devices", {"name": "r2"})["id"]
    api_post(server, f"/api/devices/{r1}/configs", {"text": BASELINE, "source_name": "r1"})
    local = SECOND.replace("network 10.12.0.0/30 area 0.0.0.0",
                           "network 10.12.0.0/30 area 0.0.0.1")
    api_post(server, f"/api/devices/{r2}/configs", {"text": local, "source_name": "r2"})
    api_post(server, "/api/links", {"device_a_id": r1, "iface_a": "eth2", "device_b_id": r2, "iface_b": "eth1"})
    api_post(server, f"/api/devices/{r2}/diagnosis-imports",
             {"bundle": _area_diagnosis_bundle(local, BASELINE)})

    page.goto(server)
    page.select_option("[data-testid=device-select]", r2)
    page.wait_for_selector("[data-testid=diagnosis-import]")
    page.locator("[data-testid=diagnosis-import]").first.click()
    page.click("[data-testid=open-findings]")
    page.wait_for_selector("[data-testid=diagnosis-findings]")
    assert "R005" in page.locator("[data-testid=diagnosis-findings]").inner_text()
    page.locator("[data-testid=diagnosis-finding-line]").first.click()
    page.wait_for_function("() => document.querySelector('[data-testid=evidence-body]')"
                           "?.textContent?.includes('area')")


def test_diagnosis_import_is_visible_from_device_detail(page, server):
    device = api_post(server, "/api/devices", {"name": "r2"})["id"]
    api_post(server, f"/api/devices/{device}/diagnosis-imports",
             {"bundle": _diagnosis_bundle(BASELINE, node="r2")})

    page.goto(server)
    page.select_option("[data-testid=device-select]", device)
    page.wait_for_selector("[data-testid=diagnosis-imports] [data-testid=diagnosis-import]")
    card = page.locator("[data-testid=diagnosis-imports]").inner_text()
    assert "normal" in card and "两端 Area 一致" in card

    page.locator("[data-testid=diagnosis-import]").first.click()
    page.wait_for_selector("[data-testid=diagnosis-evidence]")
    detail = page.locator("[data-testid=diagnosis-evidence]").inner_text()
    assert "E1" in detail and "query_interface_config" in detail

    page.click("[data-testid=open-version]")
    page.wait_for_selector("[data-testid=diagnosis-version-text]")
    version_text = page.locator("[data-testid=diagnosis-version-text]").inner_text()
    assert "hostname r1" in version_text


def _upload_bundle(page, tmp_path, name: str, bundle: dict) -> None:
    path = tmp_path / name
    path.write_text(json.dumps(bundle), encoding="utf-8")
    page.set_input_files("[data-testid=diagnosis-upload-input]", str(path))
    page.click("[data-testid=diagnosis-upload-submit]")


def test_upload_normal_bundle_stores_evidence_only(page, server, tmp_path):
    device = api_post(server, "/api/devices", {"name": "r2"})["id"]
    page.goto(server)
    page.select_option("[data-testid=device-select]", device)
    _upload_bundle(page, tmp_path, "normal.json", _diagnosis_bundle(BASELINE, node="r2"))
    page.wait_for_selector("[data-testid=diagnosis-result-nodraft]")
    assert "仅存证" in page.locator("[data-testid=diagnosis-result-nodraft]").inner_text()
    changes = httpx.get(server + f"/api/devices/{device}/change-requests",
                        cookies=api_login(server)).json()["data"]
    assert changes == []


def test_upload_area_bundle_creates_local_r005_draft(page, server, tmp_path):
    r1 = api_post(server, "/api/devices", {"name": "r1"})["id"]
    r2 = api_post(server, "/api/devices", {"name": "r2"})["id"]
    api_post(server, "/api/links", {"device_a_id": r1, "iface_a": "eth2", "device_b_id": r2, "iface_b": "eth1"})
    local = SECOND.replace("network 10.12.0.0/30 area 0.0.0.0",
                           "network 10.12.0.0/30 area 0.0.0.1")
    page.goto(server)
    page.select_option("[data-testid=device-select]", r2)
    _upload_bundle(page, tmp_path, "area.json", _area_diagnosis_bundle(local, BASELINE))
    page.wait_for_selector("[data-testid=diagnosis-result-draft]")
    assert "已生成草稿" in page.locator("[data-testid=diagnosis-result-draft]").inner_text()
    changes = httpx.get(server + f"/api/devices/{r2}/change-requests",
                        cookies=api_login(server)).json()["data"]
    assert len(changes) == 1 and changes[0]["status"] == "draft"
    assert changes[0]["finding_ids"]


def test_upload_device_mismatch_shows_server_reason(page, server, tmp_path):
    device = api_post(server, "/api/devices", {"name": "other"})["id"]
    page.goto(server)
    page.select_option("[data-testid=device-select]", device)
    _upload_bundle(page, tmp_path, "mismatch.json", _diagnosis_bundle(BASELINE, node="r2"))
    page.wait_for_selector("[data-testid=diagnosis-error]")
    text = page.locator("[data-testid=diagnosis-error]").inner_text()
    assert "DEVICE_NODE_MISMATCH" in text
    imports = httpx.get(server + f"/api/devices/{device}/diagnosis-imports",
                        cookies=api_login(server)).json()["data"]
    assert imports == []


def test_upload_same_bundle_twice_returns_original_record(page, server, tmp_path):
    device = api_post(server, "/api/devices", {"name": "r2"})["id"]
    page.goto(server)
    page.select_option("[data-testid=device-select]", device)
    bundle = _diagnosis_bundle(BASELINE, node="r2")
    _upload_bundle(page, tmp_path, "normal.json", bundle)
    page.wait_for_selector("[data-testid=diagnosis-result-nodraft]")
    _upload_bundle(page, tmp_path, "normal-again.json", bundle)
    page.wait_for_selector("[data-testid=diagnosis-result-duplicate]")
    assert "返回原记录" in page.locator("[data-testid=diagnosis-result-duplicate]").inner_text()
    imports = httpx.get(server + f"/api/devices/{device}/diagnosis-imports",
                        cookies=api_login(server)).json()["data"]
    assert len(imports) == 1

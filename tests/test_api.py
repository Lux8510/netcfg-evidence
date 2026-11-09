from __future__ import annotations

from conftest import create_device, import_config


def test_health(app_client):
    assert app_client.get("/api/health").json()["data"]["status"] == "ok"


def test_create_and_list_devices(app_client):
    device_id = create_device(app_client, "r1")
    duplicate = app_client.post("/api/devices", json={"name": "r1"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DEVICE_EXISTS"

    items = app_client.get("/api/devices").json()["data"]
    assert [(item["id"], item["name"]) for item in items] == [(device_id, "r1")]


def test_import_parses_and_is_idempotent(app_client, r1_text):
    device_id = create_device(app_client)
    first = import_config(app_client, device_id, r1_text)
    assert first.status_code == 201, first.text
    payload = first.json()["data"]
    assert payload["unchanged"] is False
    assert payload["parsed"]["hostname"] == "r1"
    assert payload["unparsed"] == []
    assert payload["line_count"] == 35

    replay = import_config(app_client, device_id, r1_text)
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == payload["id"]
    assert replay.json()["data"]["unchanged"] is True

    versions = app_client.get(f"/api/devices/{device_id}/configs").json()["data"]
    assert len(versions) == 1


def test_version_detail_includes_original_text(app_client, r1_text):
    device_id = create_device(app_client)
    version_id = import_config(app_client, device_id, r1_text).json()["data"]["id"]
    detail = app_client.get(f"/api/versions/{version_id}").json()["data"]
    assert detail["text"] == r1_text
    assert detail["parsed"]["interfaces"]["eth2"]["ospf"]["cost"] == 10


def test_unparsed_lines_are_kept(app_client):
    device_id = create_device(app_client)
    text = "hostname x\ninterface eth1\n ip address 10.0.0.1/30\n ip mtu 1400\n!\n"
    payload = import_config(app_client, device_id, text).json()["data"]
    assert payload["unparsed"] == [{"line": 4, "text": "ip mtu 1400"}]


def test_drift_requires_baseline(app_client, r1_text):
    device_id = create_device(app_client)
    import_config(app_client, device_id, r1_text)
    response = app_client.post(f"/api/devices/{device_id}/drift", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_BASELINE"


def test_full_drift_flow(app_client, r1_text):
    device_id = create_device(app_client)
    baseline_version = import_config(app_client, device_id, r1_text).json()["data"]["id"]
    mutated = r1_text.replace(" ip ospf cost 10", " ip ospf cost 20")
    current_version = import_config(app_client, device_id, mutated).json()["data"]["id"]

    setup = app_client.post(f"/api/devices/{device_id}/baseline", json={"version_id": baseline_version})
    assert setup.status_code == 201, setup.text
    assert app_client.get(f"/api/devices/{device_id}/baseline").json()["data"]["config_version_id"] == baseline_version

    report = app_client.post(f"/api/devices/{device_id}/drift", json={})
    assert report.status_code == 201, report.text
    data = report.json()["data"]
    assert data["identical"] is False
    assert {key: data["summary"][key] for key in ("added", "removed", "changed", "total")} == {
        "added": 0, "removed": 0, "changed": 1, "total": 1}
    assert data["summary"]["unparsed_baseline"] == 0
    assert data["current_version_id"] == current_version
    item = data["items"][0]
    assert item["path"] == "interface.eth2.ospf.cost"
    assert (item["old"], item["new"]) == (10, 20)
    assert item["old_lines"] == [14] and item["new_lines"] == [14]

    stored = app_client.get(f"/api/drift/{data['id']}").json()["data"]
    assert stored["items"] == data["items"]

    history = app_client.get(f"/api/devices/{device_id}/drift").json()["data"]
    assert [entry["id"] for entry in history] == [data["id"]]


def test_drift_with_explicit_current_version_and_no_change(app_client, r1_text):
    device_id = create_device(app_client)
    version_id = import_config(app_client, device_id, r1_text).json()["data"]["id"]
    app_client.post(f"/api/devices/{device_id}/baseline", json={"version_id": version_id})
    report = app_client.post(f"/api/devices/{device_id}/drift", json={"current_version_id": version_id})
    assert report.status_code == 201
    assert report.json()["data"]["identical"] is True
    assert report.json()["data"]["summary"]["total"] == 0


def test_baseline_replacement_keeps_single_active(app_client, r1_text):
    device_id = create_device(app_client)
    first = import_config(app_client, device_id, r1_text).json()["data"]["id"]
    second = import_config(app_client, device_id, r1_text.replace("hostname r1", "hostname r1x")).json()["data"]["id"]
    app_client.post(f"/api/devices/{device_id}/baseline", json={"version_id": first})
    app_client.post(f"/api/devices/{device_id}/baseline", json={"version_id": second})
    baseline = app_client.get(f"/api/devices/{device_id}/baseline").json()["data"]
    assert baseline["config_version_id"] == second


def test_errors_for_missing_resources(app_client, r1_text):
    assert app_client.get("/api/devices/missing/configs").status_code == 404
    assert app_client.get("/api/versions/missing").status_code == 404
    assert app_client.get("/api/drift/missing").status_code == 404
    device_id = create_device(app_client)
    version_id = import_config(app_client, device_id, r1_text).json()["data"]["id"]
    other = create_device(app_client, "r2")
    mismatch = app_client.post(f"/api/devices/{other}/baseline", json={"version_id": version_id})
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "VERSION_MISMATCH"


def test_validation_rejects_unknown_fields(app_client, r1_text):
    device_id = create_device(app_client)
    response = app_client.post(f"/api/devices/{device_id}/configs",
                               json={"text": r1_text, "source_name": "x", "unexpected": 1})
    assert response.status_code == 422


def test_unusable_config_returns_422(app_client):
    device_id = create_device(app_client)
    response = app_client.post(f"/api/devices/{device_id}/configs",
                               json={"text": "hostname x\x00", "source_name": "bad"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONFIG_UNUSABLE"


def test_rules_endpoint_lists_rule_metadata(app_client):
    rules = app_client.get("/api/rules").json()["data"]
    assert [rule["id"] for rule in rules] == ["R001", "R002", "R003", "R004"]
    assert all(rule["source"] and rule["severity"] for rule in rules)


def test_rules_check_clean_config_has_no_findings(app_client, r1_text):
    device_id = create_device(app_client)
    version_id = import_config(app_client, device_id, r1_text).json()["data"]["id"]
    result = app_client.post(f"/api/devices/{device_id}/rules/check", json={}).json()["data"]
    assert result["counts"] == {"rules_checked": 4, "rules_satisfied": 4, "rules_not_applicable": 0,
                                "findings_violated": 0, "findings_undetermined": 0}
    assert result["findings"] == []
    assert result["config_version_id"] == version_id
    assert result["digest"]["sha256"]


def test_rules_check_persists_findings_with_evidence(app_client, r1_text):
    device_id = create_device(app_client)
    broken = r1_text.replace(" ip ospf cost 10\n", "").replace(" passive-interface lo\n", "")
    version_id = import_config(app_client, device_id, broken).json()["data"]["id"]
    result = app_client.post(f"/api/devices/{device_id}/rules/check", json={}).json()["data"]
    assert result["counts"]["findings_violated"] == 2
    rule_ids = sorted(item["rule_id"] for item in result["findings"])
    assert rule_ids == ["R001", "R003"]

    stored = app_client.get(f"/api/devices/{device_id}/findings",
                            params={"run_id": result["run_id"]}).json()["data"]
    assert len(stored) == 2
    evidence = stored[0]["evidence"][0]
    assert evidence["line"] is not None and evidence["text"]

    # The evidence line must match the original text exactly.
    line = app_client.get(f"/api/versions/{version_id}/lines",
                          params={"start": evidence["line"], "end": evidence["line"]}).json()["data"]
    assert line["lines"][0]["text"].strip() == evidence["text"]


def test_rules_check_undetermined_when_ospf_line_unparsed(app_client, r1_text):
    device_id = create_device(app_client)
    text = r1_text.replace(" ip ospf cost 10\n", " ip ospf cost 10\n ip ospf bfd\n")
    import_config(app_client, device_id, text)
    result = app_client.post(f"/api/devices/{device_id}/rules/check", json={}).json()["data"]
    assert result["counts"]["findings_undetermined"] == 4
    assert {item["status"] for item in result["findings"]} == {"undetermined"}


def test_line_evidence_bounds(app_client, r1_text):
    device_id = create_device(app_client)
    version_id = import_config(app_client, device_id, r1_text).json()["data"]["id"]
    assert app_client.get(f"/api/versions/{version_id}/lines", params={"start": 999, "end": 1000}).status_code == 422
    assert app_client.get(f"/api/versions/{version_id}/lines", params={"start": 1, "end": 500}).status_code == 422
    ok = app_client.get(f"/api/versions/{version_id}/lines", params={"start": 10, "end": 11}).json()["data"]
    assert [row["line"] for row in ok["lines"]] == [10, 11]


def test_web_ui_is_served_without_shadowing_api(app_client):
    index = app_client.get("/")
    assert index.status_code == 200
    assert "netcfg-evidence" in index.text
    assert app_client.get("/app.js").status_code == 200
    assert app_client.get("/styles.css").status_code == 200
    assert app_client.get("/api/health").json()["data"]["status"] == "ok"

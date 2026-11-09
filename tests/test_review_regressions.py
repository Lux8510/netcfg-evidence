"""Regressions for review-feedback/2026-11-09-codex.md (items 01, 02, 03, 04, 06, 07, 08)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from domain.netcfg.drift import diff_configs
from domain.netcfg.parser import parse_frr_config
from domain.netcfg.rules import run_rules
from domain.netcfg import service
from engine.db import Base, make_engine
from engine.models import ConfigVersion, Device

SAMPLES = Path(__file__).resolve().parents[1] / "samples/frr"


@pytest.fixture(scope="module")
def r1_text() -> str:
    return (SAMPLES / "r1.conf").read_text(encoding="utf-8")


# [01] `no ip ospf cost` must not leave the old cost in place.
def test_no_ip_ospf_cost_removes_cost_and_points_at_the_negation(r1_text):
    text = r1_text.replace(" ip ospf cost 10\n", " ip ospf cost 10\n no ip ospf cost\n")
    parsed = parse_frr_config(text)
    assert parsed.interfaces["eth2"].ospf.cost is None
    result = run_rules(parsed)
    assert result["findings_violated"] == 1
    finding = result["findings"][0]
    assert finding.rule_id == "R001" and finding.status == "violated"
    assert finding.evidence[0].text == "no ip ospf cost"


def test_unknown_negation_makes_related_rules_undetermined(r1_text):
    text = r1_text.replace(" ip ospf cost 10\n", " ip ospf cost 10\n no ip ospf authentication\n")
    result = run_rules(parse_frr_config(text))
    assert result["findings_undetermined"] == 4
    assert result["rules_satisfied"] == 0


# [02] Address order is not normalised away.
def test_address_order_is_preserved_and_reordering_is_drift():
    base = "interface eth0\n ip address 192.0.2.1/24\n ip address 192.0.2.2/24\n!\n"
    swapped = "interface eth0\n ip address 192.0.2.2/24\n ip address 192.0.2.1/24\n!\n"
    first = parse_frr_config(base)
    second = parse_frr_config(swapped)
    assert first.canonical_sha256 != second.canonical_sha256
    report = diff_configs(first, second)
    assert [item.path for item in report.items] == ["interface.eth0.addresses"]
    assert report.items[0].old == ["192.0.2.1/24", "192.0.2.2/24"]
    assert report.items[0].new == ["192.0.2.2/24", "192.0.2.1/24"]


# [06] Evidence must point at the address that changed, and hostname carries its line.
def test_second_address_change_points_at_the_second_line():
    base = "interface eth0\n ip address 192.0.2.1/24\n ip address 198.51.100.1/24\n!\n"
    current = "interface eth0\n ip address 192.0.2.1/24\n ip address 198.51.100.2/24\n!\n"
    report = diff_configs(parse_frr_config(base), parse_frr_config(current))
    item = report.items[0]
    assert item.old_lines == (3,) and item.new_lines == (3,)


def test_hostname_change_carries_both_line_numbers():
    first = parse_frr_config("hostname a\ninterface e0\n!\n")
    second = parse_frr_config("hostname b\ninterface e0\n!\n")
    report = diff_configs(first, second)
    assert report.items[0].path == "hostname"
    assert report.items[0].old_lines == (1,) and report.items[0].new_lines == (1,)


def test_removed_address_points_at_the_removed_line():
    base = "interface eth0\n ip address 192.0.2.1/24\n ip address 198.51.100.1/24\n!\n"
    current = "interface eth0\n ip address 192.0.2.1/24\n!\n"
    report = diff_configs(parse_frr_config(base), parse_frr_config(current))
    item = report.items[0]
    assert item.old_lines == (3,)


# [03] Concurrent identical imports resolve to the winner instead of a 500.
def test_persist_version_recovers_from_unique_race(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'race.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    text = "hostname r1\ninterface e0\n ip address 10.0.0.1/24\n!\n"
    with factory() as one, factory() as two:
        device = service.create_device(one, "r1")
        one.commit()
        parsed = parse_frr_config(text)
        first, created = service._persist_version(two, two.get(Device, device.id), parsed)
        two.commit()
        assert created is True

        second, created_again = service._persist_version(one, one.get(Device, device.id), parsed)
        assert created_again is False
        assert second.id == first.id


# [04] Concurrent baseline activation returns a conflict, not a 500.
def test_activate_baseline_race_returns_conflict_or_the_winner(tmp_path, r1_text):
    engine = make_engine(f"sqlite:///{tmp_path / 'baseline.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        device = service.create_device(setup, "r1")
        versions = []
        for text in (r1_text, r1_text.replace("hostname r1", "hostname r1b"),
                     r1_text.replace("hostname r1", "hostname r1c")):
            version, _ = service.import_config(setup, device.id, text, "r1.conf")
            versions.append(version)
        setup.commit()
        service.set_baseline(setup, device.id, versions[0].id)
        setup.commit()

    with factory() as stale, factory() as other:
        service.set_baseline(other, device.id, versions[1].id)
        other.commit()
        winner = service._activate_baseline(stale, device.id, versions[1])
        assert winner.config_version_id == versions[1].id

    with factory() as stale2, factory() as other2:
        service.set_baseline(other2, device.id, versions[1].id)
        other2.commit()
        with pytest.raises(service.ServiceError) as excinfo:
            service._activate_baseline(stale2, device.id, versions[2])
        assert excinfo.value.code == "BASELINE_CONFLICT"
        assert excinfo.value.status == 409


# [07] Line evidence bounds are inclusive and validated, not silently clamped.
def test_line_bounds_are_validated(app_client):
    device_id = app_client.post("/api/devices", json={"name": "lines"}).json()["data"]["id"]
    text = "hostname r1\n" + "".join(f"! line {number}\n" for number in range(2, 202))
    version_id = app_client.post(f"/api/devices/{device_id}/configs",
                                 json={"text": text, "source_name": "long"}).json()["data"]["id"]
    assert app_client.get(f"/api/versions/{version_id}/lines",
                          params={"start": 0, "end": 1}).status_code == 422
    assert app_client.get(f"/api/versions/{version_id}/lines",
                          params={"start": 1, "end": 201}).status_code == 422
    ok = app_client.get(f"/api/versions/{version_id}/lines",
                        params={"start": 1, "end": 200}).json()["data"]
    assert len(ok["lines"]) == 200
    assert app_client.get(f"/api/versions/{version_id}/lines",
                          params={"start": 500, "end": 501}).status_code == 422


# [08] Declared-but-unparsed OSPF and missing devices must not look like success.
def test_declared_but_unparsed_ospf_is_undetermined():
    result = run_rules(parse_frr_config("hostname x\nrouter ospf 1\n!\n"))
    assert result["findings_undetermined"] == 4
    assert result["rules_satisfied"] == 0


def test_config_without_ospf_reports_not_applicable():
    result = run_rules(parse_frr_config("hostname x\ninterface e0\n ip address 10.0.0.1/24\n!\n"))
    assert result["rules_not_applicable"] == 4
    assert result["rules_satisfied"] == 0


def test_missing_device_baseline_is_404(app_client):
    assert app_client.get("/api/devices/missing/baseline").status_code == 404


def test_drift_reports_unparsed_counts(app_client, r1_text):
    device_id = app_client.post("/api/devices", json={"name": "r1"}).json()["data"]["id"]
    baseline = app_client.post(f"/api/devices/{device_id}/configs",
                               json={"text": r1_text, "source_name": "r1.conf"}).json()["data"]["id"]
    current_text = r1_text + "\ninterface eth9\n ip mtu 1400\n!\n"
    app_client.post(f"/api/devices/{device_id}/configs", json={"text": current_text, "source_name": "r1b"})
    app_client.post(f"/api/devices/{device_id}/baseline", json={"version_id": baseline})
    report = app_client.post(f"/api/devices/{device_id}/drift", json={}).json()["data"]
    assert report["summary"]["unparsed_baseline"] == 0
    assert report["summary"]["unparsed_current"] == 1

"""Cross-device rules: area consistency, duplicate addresses, subnet mismatch."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from domain.netcfg import cross_rules, service
from domain.netcfg.parser import parse_frr_config
from engine.db import Base, make_engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def texts() -> dict[str, str]:
    return {
        "r1": (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8"),
        "r2": (ROOT / "samples/frr/r2.conf").read_text(encoding="utf-8"),
    }


@pytest.fixture()
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'cross.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def peer(parsed, version_id="v2", name="r2", interface="eth1") -> cross_rules.Peer:
    return cross_rules.Peer(device_id="dev-b", device_name=name, version_id=version_id,
                            interface=interface, parsed=parsed)


def test_matching_link_has_no_findings(texts):
    r1 = parse_frr_config(texts["r1"], "r1")
    r2 = parse_frr_config(texts["r2"], "r2")
    findings = cross_rules.check_link(r1, "v1", "eth2", peer(r2))
    assert findings == []


def test_area_mismatch_between_ends(texts):
    r1 = parse_frr_config(texts["r1"], "r1")
    r2 = parse_frr_config(texts["r2"].replace("network 10.12.0.0/30 area 0.0.0.0",
                                             "network 10.12.0.0/30 area 0.0.0.1"), "r2")
    findings = cross_rules.check_link(r1, "v1", "eth2", peer(r2))
    assert [(f.rule_id, f.status) for f in findings] == [("R005", "violated")]
    assert {item.version_id for item in findings[0].evidence} == {"v1", "v2"}


def test_link_subnet_mismatch(texts):
    r1 = parse_frr_config(texts["r1"], "r1")
    moved = texts["r2"].replace(" ip address 10.12.0.2/30", " ip address 10.12.1.2/30").replace(
        " network 10.12.0.0/30 area 0.0.0.0", " network 10.12.1.0/30 area 0.0.0.0")
    r2 = parse_frr_config(moved, "r2")
    findings = cross_rules.check_link(r1, "v1", "eth2", peer(r2))
    assert [(f.rule_id, f.status) for f in findings] == [("R007", "violated")]


def test_missing_area_is_undetermined(texts):
    r1 = parse_frr_config(texts["r1"], "r1")
    r2 = parse_frr_config("hostname r2\ninterface eth1\n ip address 10.12.0.2/30\n!\n", "r2")
    findings = cross_rules.check_link(r1, "v1", "eth2", peer(r2))
    assert [f.status for f in findings] == ["undetermined"]


def test_duplicate_address_across_devices(texts):
    r1 = parse_frr_config(texts["r1"], "r1")
    r2 = parse_frr_config(texts["r2"].replace(" ip address 10.12.0.2/30", " ip address 10.12.0.1/30"), "r2")
    findings = cross_rules.check_duplicate_addresses(r1, "v1", [peer(r2)])
    assert [(f.rule_id, f.status) for f in findings] == [("R006", "violated")]
    assert {item.version_id for item in findings[0].evidence} == {"v1", "v2"}


def test_api_cross_check_flow(app_client, texts):
    def post(path, payload):
        response = app_client.post(path, json=payload)
        assert response.status_code < 400, response.text
        return response.json()["data"]

    a = post("/api/devices", {"name": "r1"})["id"]
    b = post("/api/devices", {"name": "r2"})["id"]
    post(f"/api/devices/{a}/configs", {"text": texts["r1"], "source_name": "r1"})
    post(f"/api/devices/{b}/configs", {"text": texts["r2"], "source_name": "r2"})
    link = post("/api/links", {"device_a_id": a, "iface_a": "eth2", "device_b_id": b, "iface_b": "eth1"})
    assert link["device_a"]["name"] == "r1"

    local_only = post(f"/api/devices/{a}/rules/check", {})
    assert local_only["counts"]["rules_checked"] == 4

    cross = post(f"/api/devices/{a}/rules/check", {"include_cross": True})
    assert cross["counts"]["rules_checked"] == 7
    assert cross["counts"]["rules_not_applicable"] == 0
    assert [f["rule_id"] for f in cross["findings"]] == []

    # Change the peer's area and re-check: R005 becomes violated with both versions in evidence.
    post(f"/api/devices/{b}/configs", {
        "text": texts["r2"].replace("network 10.12.0.0/30 area 0.0.0.0", "network 10.12.0.0/30 area 0.0.0.1"),
        "source_name": "r2-changed"})
    changed = post(f"/api/devices/{a}/rules/check", {"include_cross": True})
    finding = next(item for item in changed["findings"] if item["rule_id"] == "R005")
    assert finding["status"] == "violated"
    assert {item["version_id"] for item in finding["evidence"] if item.get("version_id")}


def test_no_links_makes_cross_rules_not_applicable(app_client, texts):
    device = app_client.post("/api/devices", json={"name": "solo"}).json()["data"]["id"]
    app_client.post(f"/api/devices/{device}/configs", json={"text": texts["r1"], "source_name": "solo"})
    result = app_client.post(f"/api/devices/{device}/rules/check", json={"include_cross": True}).json()["data"]
    assert result["counts"]["rules_not_applicable"] == 3
    assert result["counts"]["rules_checked"] == 7

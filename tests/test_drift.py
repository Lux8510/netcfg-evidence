from __future__ import annotations

from pathlib import Path

import pytest

from domain.netcfg.drift import diff_configs
from domain.netcfg.parser import parse_frr_config

SAMPLES = Path(__file__).resolve().parents[1] / "samples/frr"


@pytest.fixture(scope="module")
def baseline_text() -> str:
    return (SAMPLES / "r1.conf").read_text(encoding="utf-8")


def mutate(text: str, old: str, new: str) -> str:
    assert old in text, f"mutation target missing: {old!r}"
    return text.replace(old, new, 1)


def report(baseline: str, current: str):
    return diff_configs(parse_frr_config(baseline, "baseline.conf"), parse_frr_config(current, "current.conf"))


def test_identical_config_reports_no_drift(baseline_text):
    result = report(baseline_text, baseline_text)
    assert result.identical is True
    assert result.summary()["total"] == 0
    assert result.baseline_hash == result.current_hash


def test_cost_change_is_reported_with_line_numbers(baseline_text):
    current = mutate(baseline_text, " ip ospf cost 10", " ip ospf cost 20")
    result = report(baseline_text, current)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.path == "interface.eth2.ospf.cost"
    assert item.kind == "changed"
    assert (item.old, item.new) == (10, 20)
    assert item.old_lines == (14,)
    assert item.new_lines == (14,)


def test_area_change_is_reported(baseline_text):
    current = mutate(baseline_text, "network 10.12.0.0/30 area 0.0.0.0", "network 10.12.0.0/30 area 0.0.0.1")
    result = report(baseline_text, current)
    paths = [item.path for item in result.items]
    assert paths == ["ospf.networks"]
    item = result.items[0]
    assert {"prefix": "10.12.0.0/30", "area": "0.0.0.1"} in item.new


def test_removed_network_is_reported(baseline_text):
    current = mutate(baseline_text, " network 10.13.0.0/30 area 0.0.0.0\n", "")
    result = report(baseline_text, current)
    assert [item.path for item in result.items] == ["ospf.networks"]


def test_passive_interface_change_is_reported(baseline_text):
    current = mutate(baseline_text, " passive-interface eth1\n", "")
    result = report(baseline_text, current)
    assert [item.path for item in result.items] == ["ospf.passive_interfaces"]
    assert result.items[0].old == ["eth1", "lo"]
    assert result.items[0].new == ["lo"]


def test_interface_added_and_removed(baseline_text):
    added = baseline_text + "\ninterface eth9\n description new\n ip address 10.99.0.1/30\n!\n"
    result = report(baseline_text, added)
    assert [item.kind for item in result.items] == ["added"]
    assert result.items[0].path == "interface.eth9"

    removed = mutate(baseline_text, "interface eth3\n description r1-to-r3-backup\n ip address 10.13.0.1/30\n ip ospf network point-to-point\n ip ospf cost 50\n!\n", "")
    result = report(baseline_text, removed)
    kinds = {(item.kind, item.path) for item in result.items}
    assert ("removed", "interface.eth3") in kinds
    # Removing the interface also removes its OSPF network statement? No: it stays, so no extra drift.
    assert len(result.items) == 1


def test_shutdown_and_description(baseline_text):
    current = mutate(baseline_text, " description r1-to-r2-primary", " description disabled-by-change")
    current = mutate(current, " ip address 10.12.0.1/30", " ip address 10.12.0.1/30\n shutdown")
    result = report(baseline_text, current)
    paths = {item.path for item in result.items}
    assert paths == {"interface.eth2.description", "interface.eth2.shutdown"}


def test_ospf_process_removed_and_added(baseline_text):
    without = baseline_text.replace("router ospf\n ospf router-id 10.255.1.1\n network 10.101.0.0/30 area 0.0.0.0\n network 10.12.0.0/30 area 0.0.0.0\n network 10.13.0.0/30 area 0.0.0.0\n network 10.255.1.1/32 area 0.0.0.0\n passive-interface eth1\n passive-interface lo\n!\n", "")
    assert "router ospf" not in without
    result = report(baseline_text, without)
    assert [item.path for item in result.items] == ["ospf"]
    assert result.items[0].kind == "removed"

    added = report(without, baseline_text)
    assert added.items[0].kind == "added"


def test_hostname_change(baseline_text):
    current = mutate(baseline_text, "hostname r1", "hostname r1-renamed")
    result = report(baseline_text, current)
    assert [(item.path, item.kind) for item in result.items] == [("hostname", "changed")]


def test_drift_report_is_json_ready(baseline_text):
    current = mutate(baseline_text, " ip ospf cost 10", " ip ospf cost 20")
    payload = report(baseline_text, current).as_dict()
    assert payload["identical"] is False
    assert payload["summary"] == {"added": 0, "removed": 0, "changed": 1, "total": 1}
    assert payload["items"][0]["new_lines"] == [14]

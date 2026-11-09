from __future__ import annotations

from pathlib import Path

import pytest

from domain.netcfg.parser import parse_frr_config
from domain.netcfg.rules import check_loopback_passive, check_network_matches_interface, check_p2p_requires_cost, check_router_id_is_loopback, run_rules

SAMPLES = Path(__file__).resolve().parents[1] / "samples/frr"


@pytest.fixture(scope="module")
def r1_text() -> str:
    return (SAMPLES / "r1.conf").read_text(encoding="utf-8")


def test_clean_baseline_satisfies_every_rule(r1_text):
    result = run_rules(parse_frr_config(r1_text))
    assert result["rules_checked"] == 4
    assert result["rules_satisfied"] == 4
    assert result["findings"] == []


def test_missing_cost_on_point_to_point(r1_text):
    text = r1_text.replace(" ip ospf cost 10\n", "")
    findings = check_p2p_requires_cost(parse_frr_config(text))
    assert [(f.rule_id, f.status) for f in findings] == [("R001", "violated")]
    assert "eth2" in findings[0].message
    assert findings[0].evidence[0].line is not None


def test_network_declared_without_matching_interface(r1_text):
    text = r1_text.replace(" network 10.13.0.0/30 area 0.0.0.0", " network 10.77.0.0/30 area 0.0.0.0")
    findings = check_network_matches_interface(parse_frr_config(text))
    assert len(findings) == 1
    assert "10.77.0.0/30" in findings[0].message
    assert findings[0].evidence[0].path == "ospf.network.10.77.0.0/30"


def test_loopback_not_passive(r1_text):
    text = r1_text.replace(" passive-interface lo\n", "")
    findings = check_loopback_passive(parse_frr_config(text))
    assert len(findings) == 1
    assert "lo" in findings[0].message


def test_router_id_not_on_loopback(r1_text):
    text = r1_text.replace(" ospf router-id 10.255.1.1", " ospf router-id 1.1.1.1")
    findings = check_router_id_is_loopback(parse_frr_config(text))
    assert len(findings) == 1
    assert "1.1.1.1" in findings[0].message


def test_unparsed_ospf_line_makes_rule_undetermined(r1_text):
    text = r1_text.replace(" ip ospf cost 10\n", " ip ospf cost 10\n ip ospf bfd\n")
    parsed = parse_frr_config(text)
    assert [line.text for line in parsed.unparsed] == ["ip ospf bfd"]
    findings = check_p2p_requires_cost(parsed)
    assert [(f.rule_id, f.status) for f in findings] == [("R001", "undetermined")]
    result = run_rules(parsed)
    assert result["findings_undetermined"] == 4
    assert result["rules_satisfied"] == 0


def test_unparsed_unrelated_line_does_not_change_results(r1_text):
    text = r1_text + "\ninterface eth9\n description spare\n shutdown\n!\n"
    parsed = parse_frr_config(text)
    result = run_rules(parsed)
    assert result["findings"] == []


def test_multiple_violations_are_all_reported(r1_text):
    text = r1_text.replace(" ip ospf cost 10\n", "").replace(" passive-interface lo\n", "").replace(
        " ospf router-id 10.255.1.1", " ospf router-id 2.2.2.2")
    result = run_rules(parse_frr_config(text))
    rule_ids = sorted(f.rule_id for f in result["findings"])
    assert rule_ids == ["R001", "R003", "R004"]
    assert result["findings_violated"] == 3

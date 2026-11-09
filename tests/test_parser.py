from __future__ import annotations

from pathlib import Path

import pytest

from domain.netcfg.parser import ConfigParseError, canonical_hash, parse_frr_config

SAMPLES = Path(__file__).resolve().parents[1] / "samples/frr"


def load(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def r1():
    return parse_frr_config(load("r1.conf"), "r1.conf")


def test_parses_hostname_and_interfaces(r1):
    assert r1.hostname == "r1"
    assert set(r1.interfaces) == {"eth1", "eth2", "eth3", "lo"}
    assert r1.interfaces["eth2"].description == "r1-to-r2-primary"
    assert r1.interfaces["eth2"].addresses == ["10.12.0.1/30"]


def test_parses_ospf_interface_attributes(r1):
    eth2 = r1.interfaces["eth2"].ospf
    assert eth2.cost == 10
    assert eth2.network_type == "point-to-point"
    assert eth2.area is None
    assert r1.interfaces["eth1"].ospf.cost is None


def test_parses_ospf_process(r1):
    assert r1.ospf is not None
    assert r1.ospf.router_id == "10.255.1.1"
    assert sorted(r1.ospf.passive_interfaces) == ["eth1", "lo"]
    assert len(r1.ospf.networks) == 4
    assert {(n.prefix, n.area) for n in r1.ospf.networks} == {
        ("10.101.0.0/30", "0.0.0.0"),
        ("10.12.0.0/30", "0.0.0.0"),
        ("10.13.0.0/30", "0.0.0.0"),
        ("10.255.1.1/32", "0.0.0.0"),
    }


def test_keeps_source_line_numbers_for_evidence(r1):
    assert r1.interfaces["eth2"].start_line == 10
    assert r1.interfaces["eth2"].ospf.lines["cost"] == 14
    assert r1.ospf is not None and r1.ospf.lines["router_id"] == 26


def test_real_sample_has_no_unparsed_lines(r1):
    assert r1.unparsed == []


def test_unknown_line_is_recorded_not_swallowed():
    text = "hostname x\ninterface eth1\n ip address 10.0.0.1/30\n ip mtu 1400\n!\n"
    parsed = parse_frr_config(text, "x.conf")
    assert [(line.number, line.text) for line in parsed.unparsed] == [(4, "ip mtu 1400")]


def test_unknown_top_level_line_is_recorded():
    parsed = parse_frr_config("hostname x\nrouter bgp 65000\n!\n", "x.conf")
    assert [(line.number, line.text) for line in parsed.unparsed] == [(2, "router bgp 65000")]


def test_duplicate_interface_is_reported():
    text = "interface eth1\n ip address 10.0.0.1/30\n!\ninterface eth1\n ip address 10.0.0.2/30\n!\n"
    parsed = parse_frr_config(text, "dup.conf")
    assert len(parsed.interfaces) == 1
    assert [(line.number, line.text) for line in parsed.unparsed] == [(4, "interface eth1")]


def test_raw_hash_changes_but_canonical_hash_is_stable():
    base = load("r1.conf")
    commented = "\n".join(("! generated copy" if line == "!" else line) for line in base.splitlines())
    reordered = "\n".join(reversed(base.splitlines()))

    one = parse_frr_config(base)
    two = parse_frr_config(commented)
    assert one.raw_sha256 != two.raw_sha256
    assert one.canonical_sha256 == two.canonical_sha256

    # Reordering whole blocks must not change the canonical form.
    blocks = [block for block in base.split("\n!\n") if block.strip()]
    shuffled = "\n!\n".join(reversed(blocks))
    three = parse_frr_config(shuffled)
    assert three.canonical()["interfaces"] == one.canonical()["interfaces"]
    assert three.ospf is not None and one.ospf is not None
    assert three.ospf.canonical() == one.ospf.canonical()


def test_canonical_hash_matches_helper(r1):
    assert canonical_hash(r1.canonical()) == r1.canonical_sha256


def test_rejects_unusable_input():
    with pytest.raises(ConfigParseError):
        parse_frr_config(None)  # type: ignore[arg-type]
    with pytest.raises(ConfigParseError):
        parse_frr_config("hostname x\x00")


def test_passive_default_and_default_information_are_parsed():
    text = "router ospf\n ospf router-id 1.1.1.1\n passive-interface default\n default-information originate\n!\n"
    parsed = parse_frr_config(text)
    assert parsed.ospf is not None
    assert parsed.ospf.passive_default is True
    assert parsed.ospf.default_information is True


def test_interface_passive_keyword_is_parsed():
    text = "interface eth1\n ip ospf passive\n!\n"
    parsed = parse_frr_config(text)
    assert parsed.interfaces["eth1"].ospf.passive is True

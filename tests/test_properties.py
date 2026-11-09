"""Property-based tests for the parser, drift and explanation validator.

These complement the hand-written cases: they look for invariants that should
hold for *any* input in the declared grammar subset.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from domain.netcfg.drift import diff_configs
from domain.netcfg.explain import Citation, Explanation, validate_explanation
from domain.netcfg.parser import parse_frr_config
from domain.netcfg.rules import RULES, Evidence, Finding, run_rules

SETTINGS = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])

interface_names = st.from_regex(r"[a-z]{1,3}[0-9]{1,2}", fullmatch=True)
ipv4 = st.builds(lambda a, b, c, d: f"{a}.{b}.{c}.{d}",
                 st.integers(1, 223), st.integers(0, 255), st.integers(0, 255), st.integers(1, 254))
prefix = st.builds(lambda ip, mask: f"{ip}/{mask}", ipv4, st.sampled_from([24, 30, 32]))
area = st.sampled_from(["0.0.0.0", "0.0.0.1", "0.0.0.2"])
cost = st.integers(1, 1000)


@st.composite
def configs(draw):
    names = draw(st.lists(interface_names, min_size=1, max_size=3, unique=True))
    blocks = []
    networks = []
    for name in names:
        address = draw(prefix)
        block = [f"interface {name}", f" ip address {address}"]
        if draw(st.booleans()):
            block.append(f" description {name}-uplink")
        if draw(st.booleans()):
            block.append(" ip ospf network point-to-point")
            block.append(f" ip ospf cost {draw(cost)}")
        blocks.append("\n".join(block))
        if address.endswith("/24"):
            networks.append((f"{address.rsplit('.', 1)[0]}.0/24", draw(area)))
        elif address.endswith("/30"):
            last = int(address.split(".")[3].split("/")[0])
            base = address.rsplit(".", 1)[0]
            networks.append((f"{base}.{(last // 4) * 4}/30", draw(area)))
        else:
            networks.append((address, draw(area)))
    ospf = ["router ospf"]
    ospf.append(f" ospf router-id {draw(ipv4)}")
    for network, network_area in networks:
        ospf.append(f" network {network} area {network_area}")
    if draw(st.booleans()):
        ospf.extend(f" passive-interface {name}" for name in names)
    blocks.append("\n".join(ospf))
    return "hostname prop\n" + "\n!\n".join(blocks) + "\n!\n", names, networks


@SETTINGS
@given(configs())
def test_parsing_is_deterministic(case):
    text, _, _ = case
    first = parse_frr_config(text)
    second = parse_frr_config(first.raw_text)
    assert first.canonical_sha256 == second.canonical_sha256
    assert first.canonical() == second.canonical()


@SETTINGS
@given(configs())
def test_comments_and_blank_lines_do_not_change_semantics(case):
    text, _, _ = case
    decorated = "\n".join(("! note" if line == "!" else line) for line in text.splitlines()) + "\n\n"
    assert parse_frr_config(text).canonical_sha256 == parse_frr_config(decorated).canonical_sha256


@SETTINGS
@given(configs())
def test_interface_block_order_is_irrelevant(case):
    text, names, _ = case
    parts = text.split("\n!\n")
    head, *blocks = parts
    interfaces = [block for block in blocks if block.startswith("interface ")]
    ospf = [block for block in blocks if block.startswith("router ospf")]
    shuffled = head + "\n!\n" + "\n!\n".join(list(reversed(interfaces)) + ospf)
    assert parse_frr_config(text).canonical_sha256 == parse_frr_config(shuffled + "\n!\n").canonical_sha256


@SETTINGS
@given(configs())
def test_diff_with_itself_is_empty(case):
    text, _, _ = case
    parsed = parse_frr_config(text)
    assert diff_configs(parsed, parsed).items == ()


@SETTINGS
@given(configs(), configs())
def test_diff_paths_are_symmetric(first, second):
    left = parse_frr_config(first[0])
    right = parse_frr_config(second[0])
    forward = {item.path for item in diff_configs(left, right).items}
    backward = {item.path for item in diff_configs(right, left).items}
    assert forward == backward


@SETTINGS
@given(st.integers(1, 500))
def test_unknown_line_is_recorded_at_its_exact_position(cost_value):
    text = f"hostname x\ninterface eth1\n ip address 10.0.0.1/30\n ip mtu {cost_value}\n!\n"
    parsed = parse_frr_config(text)
    assert [(line.number, line.text) for line in parsed.unparsed] == [(4, f"ip mtu {cost_value}")]


@SETTINGS
@given(st.integers(1, 500), st.booleans())
def test_last_write_wins_for_cost_and_negation(cost_value, negate_first):
    setting = f" ip ospf cost {cost_value}"
    clearing = " no ip ospf cost"
    tail = [clearing, setting] if negate_first else [setting, clearing]
    text = "\n".join(["interface eth1", " ip ospf network point-to-point", *tail]) + "\n!\n"
    parsed = parse_frr_config(text)
    assert parsed.interfaces["eth1"].ospf.cost == (cost_value if negate_first else None)


@SETTINGS
@given(st.lists(st.integers(1, 30), min_size=1, max_size=4, unique=True))
def test_explanation_accepts_exactly_the_cited_lines(lines):
    rule = next(item for item in RULES if item.id == "R001")
    evidence = tuple(Evidence("config_line", line, "no ip ospf cost") for line in lines)
    finding = Finding(rule_id="R001", status="violated", message="缺少 cost", evidence=evidence)
    listed = "、".join(str(line) for line in sorted(lines))
    explanation = Explanation(text=f"规则 R001 第 {listed} 行显示缺少 cost。",
                              citations=(Citation("R001", tuple(sorted(lines))),))
    assert validate_explanation(explanation, finding, rule).accepted

    extra = max(lines) + 1
    overclaim = Explanation(text=f"规则 R001 第 {listed}、{extra} 行显示缺少 cost。",
                            citations=(Citation("R001", tuple(sorted(lines))),))
    assert validate_explanation(overclaim, finding, rule).reason == "line_mismatch"

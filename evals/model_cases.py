"""Build the 15 explanation tasks used for model comparison.

Cases come from existing labelled rule cases plus three cross-device findings,
so the models are asked about evidence the project already trusts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from domain.netcfg import cross_rules
from domain.netcfg.parser import parse_frr_config
from domain.netcfg.rules import RULES, Rule, run_rules

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ModelCase:
    id: str
    rule_id: str
    status: str
    message: str
    evidence: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def expected_lines(self) -> set[int]:
        return {item["line"] for item in self.evidence if item.get("line")}


def _rule_cases() -> list[ModelCase]:
    data = json.loads((ROOT / "evals/rule_cases.json").read_text(encoding="utf-8"))
    cases: list[ModelCase] = []
    for case in data["cases"]:
        parsed = parse_frr_config(case["current"], case["id"])
        result = run_rules(parsed)
        for finding in result["findings"]:
            if finding.rule_id.startswith("R00") is False:
                continue
            evidence = tuple({"line": item.line, "text": item.text, "path": item.path}
                             for item in finding.evidence if item.line)
            if not evidence:
                continue
            cases.append(ModelCase(id=f"{case['id']}-{finding.rule_id}", rule_id=finding.rule_id,
                                   status=finding.status, message=finding.message, evidence=evidence))
    return cases


def _cross_cases() -> list[ModelCase]:
    r1 = (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8")
    r2 = (ROOT / "samples/frr/r2.conf").read_text(encoding="utf-8")
    first = parse_frr_config(r1, "r1")
    cases: list[ModelCase] = []

    variants = {
        "cross-area": r2.replace("network 10.12.0.0/30 area 0.0.0.0", "network 10.12.0.0/30 area 0.0.0.1"),
        "cross-subnet": r2.replace(" ip address 10.12.0.2/30", " ip address 10.12.1.2/30").replace(
            " network 10.12.0.0/30 area 0.0.0.0", " network 10.12.1.0/30 area 0.0.0.0"),
        "cross-duplicate": r2.replace(" ip address 10.12.0.2/30", " ip address 10.12.0.1/30"),
    }
    for case_id, text in variants.items():
        peer_parsed = parse_frr_config(text, "r2")
        peer = cross_rules.Peer(device_id="dev-b", device_name="r2", version_id="v2",
                                interface="eth1", parsed=peer_parsed)
        findings = cross_rules.check_link(first, "v1", "eth2", peer)
        findings += cross_rules.check_duplicate_addresses(first, "v1", [peer])
        for finding in findings:
            if finding.status != "violated":
                continue
            evidence = tuple({"line": item.line, "text": item.text, "path": item.path}
                             for item in finding.evidence if item.line)
            cases.append(ModelCase(id=f"{case_id}-{finding.rule_id}", rule_id=finding.rule_id,
                                   status=finding.status, message=finding.message, evidence=evidence))
    return cases


def build_cases(limit: int = 15) -> list[ModelCase]:
    rule_cases = _rule_cases()
    violated = [case for case in rule_cases if case.status == "violated"][:8]
    undetermined = [case for case in rule_cases if case.status == "undetermined"][:4]
    cross = _cross_cases()[:3]
    cases = violated + undetermined + cross
    seen: set[str] = set()
    unique: list[ModelCase] = []
    for case in cases:
        if case.id in seen:
            continue
        seen.add(case.id)
        unique.append(case)
    return unique[:limit]


CROSS_RULE_TEXT = {
    "R005": "链路两端 OSPF area 必须一致；不一致时邻居无法建立。",
    "R006": "同一 IP 不能同时出现在两台设备上；否则地址冲突。",
    "R007": "链路两端接口地址必须落在同一网段。",
}


def rule_for(rule_id: str):
    local = next((rule for rule in RULES if rule.id == rule_id), None)
    if local is not None:
        return local
    title = next((name for rid, name in cross_rules.CROSS_RULES if rid == rule_id), None)
    if title is None:
        return None
    return Rule(id=rule_id, title=title, rationale=CROSS_RULE_TEXT[rule_id],
                source="内部规范（示例）", severity="error", check=lambda parsed: [])

"""Deterministic rules. No LLM: every finding is a pure function of the parsed config.

Three-valued result per rule: violated / satisfied / undetermined.
"Undetermined" exists so that an unsupported line can never be reported as "ok".
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .parser import ParsedConfig

UNCERTAIN_LINE_RE = re.compile(
    r"^(?:no\s+|router\s+ospf|ip\s+ospf|ospf\s+|area\s+|network\s+|passive-interface\s+|"
    r"redistribute\s+|max-metric\s+|timers\s+|default-information\s+)"
)


@dataclass(frozen=True)
class Evidence:
    kind: str  # config_line | parsed_value
    line: int | None
    text: str
    path: str | None = None
    version_id: str | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "line": self.line, "text": self.text,
                "path": self.path, "version_id": self.version_id}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    status: str  # violated | undetermined
    message: str
    evidence: tuple[Evidence, ...] = ()

    def as_dict(self) -> dict:
        return {"rule_id": self.rule_id, "status": self.status, "message": self.message,
                "evidence": [item.as_dict() for item in self.evidence]}


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    rationale: str
    source: str
    severity: str
    check: Callable[[ParsedConfig], list[Finding]] = field(repr=False)


def line_text(parsed: ParsedConfig, number: int | None) -> str:
    if not number or number < 1 or number > parsed.line_count:
        return ""
    lines = parsed.raw_text.splitlines()
    return lines[number - 1].strip()


def _uncertain_lines(parsed: ParsedConfig) -> list[int]:
    return [line.number for line in parsed.unparsed if UNCERTAIN_LINE_RE.match(line.text)]


def _undetermined(parsed: ParsedConfig, rule_id: str, reason: str) -> Finding | None:
    lines = _uncertain_lines(parsed)
    if not lines:
        return None
    evidence = tuple(Evidence("config_line", number, line_text(parsed, number)) for number in lines[:5])
    return Finding(rule_id, "undetermined", reason, evidence)


def _interface_networks(parsed: ParsedConfig) -> dict[str, ipaddress.IPv4Network]:
    networks: dict[str, ipaddress.IPv4Network] = {}
    for name, interface in parsed.interfaces.items():
        for address in interface.addresses:
            try:
                networks[address] = ipaddress.ip_interface(address).network
            except ValueError:
                continue
    return networks


def check_p2p_requires_cost(parsed: ParsedConfig) -> list[Finding]:
    findings: list[Finding] = []
    for name, interface in sorted(parsed.interfaces.items()):
        if interface.ospf.network_type != "point-to-point":
            continue
        if interface.ospf.cost is not None:
            continue
        evidence_line = interface.ospf.lines.get("cost") or interface.start_line
        findings.append(Finding(
            "R001", "violated",
            f"接口 {name} 声明为 point-to-point，但未显式设置 ip ospf cost；将依赖默认值。",
            (Evidence("config_line", evidence_line, line_text(parsed, evidence_line),
                      f"interface.{name}.ospf.cost"),),
        ))
    if findings:
        return findings
    undetermined = _undetermined(parsed, "R001", "存在未解析的 OSPF 相关配置，无法确认 cost 是否在别处设置。")
    return [undetermined] if undetermined else []


def check_network_matches_interface(parsed: ParsedConfig) -> list[Finding]:
    if parsed.ospf is None:
        return []
    known = set(_interface_networks(parsed).values())
    findings: list[Finding] = []
    for network in sorted(parsed.ospf.networks, key=lambda n: n.line):
        try:
            prefix = ipaddress.ip_network(network.prefix, strict=False)
        except ValueError:
            findings.append(Finding("R002", "violated", f"network 宣告 {network.prefix} 不是合法网段。",
                                    (Evidence("config_line", network.line, line_text(parsed, network.line)),)))
            continue
        if prefix not in known:
            findings.append(Finding(
                "R002", "violated",
                f"network 宣告 {network.prefix} 不匹配本机任何接口网段；可能宣告了不存在的网络。",
                (Evidence("config_line", network.line, line_text(parsed, network.line),
                          f"ospf.network.{network.prefix}"),),
            ))
    if findings:
        return findings
    undetermined = _undetermined(parsed, "R002", "存在未解析的 network/OSPF 配置，无法确认宣告是否完整。")
    return [undetermined] if undetermined else []


def check_loopback_passive(parsed: ParsedConfig) -> list[Finding]:
    if parsed.ospf is None:
        return []
    loopbacks = [name for name in parsed.interfaces if name == "lo" or name.startswith("lo")]
    if not loopbacks:
        return []
    findings: list[Finding] = []
    for name in sorted(loopbacks):
        interface = parsed.interfaces[name]
        if not interface.addresses:
            continue
        passive = parsed.ospf.passive_default or name in parsed.ospf.passive_interfaces
        if not passive:
            findings.append(Finding(
                "R003", "violated",
                f"Loopback 接口 {name} 未设置 passive，会在该接口上建立 OSPF 邻居，通常不是预期行为。",
                (Evidence("parsed_value", interface.start_line, line_text(parsed, interface.start_line),
                          f"interface.{name}.ospf.passive"),),
            ))
    if findings:
        return findings
    undetermined = _undetermined(parsed, "R003", "存在未解析的 passive/OSPF 配置，无法确认 Loopback 是否已 passive。")
    return [undetermined] if undetermined else []


def check_router_id_is_loopback(parsed: ParsedConfig) -> list[Finding]:
    if parsed.ospf is None or not parsed.ospf.router_id:
        return []
    loopback_addresses = set()
    for name, interface in parsed.interfaces.items():
        if not (name == "lo" or name.startswith("lo")):
            continue
        for address in interface.addresses:
            loopback_addresses.add(address.split("/")[0])
    if not loopback_addresses:
        return []
    if parsed.ospf.router_id in loopback_addresses:
        undetermined = _undetermined(parsed, "R004", "存在未解析的 OSPF 配置，无法确认 router-id 是否被覆盖。")
        return [undetermined] if undetermined else []
    line = parsed.ospf.lines.get("router_id")
    return [Finding(
        "R004", "violated",
        f"router-id {parsed.ospf.router_id} 不是本机 Loopback 地址（{', '.join(sorted(loopback_addresses))}）。",
        (Evidence("config_line", line, line_text(parsed, line), "ospf.router_id"),),
    )]


RULES: tuple[Rule, ...] = (
    Rule("R001", "点到点 OSPF 接口必须显式设置 cost",
         "默认 cost 由带宽推算，跨厂商/跨版本可能不一致，显式设置才能保证选路可预测。",
         "内部规范（示例）", "warning", check_p2p_requires_cost),
    Rule("R002", "network 宣告必须匹配本机接口网段",
         "宣告不存在的网段不会报错，但会让 OSPF 通告错误路由。",
         "内部规范（示例）", "error", check_network_matches_interface),
    Rule("R003", "Loopback 接口必须 passive",
         "Loopback 上建立邻居没有意义，且可能引入异常邻接关系。",
         "内部规范（示例）", "warning", check_loopback_passive),
    Rule("R004", "OSPF router-id 必须是本机 Loopback 地址",
         "router-id 不稳定会导致邻居震荡和排障困难。",
         "内部规范（示例）", "error", check_router_id_is_loopback),
)


OSPF_DECLARED_RE = re.compile(r"^\s*router\s+ospf\b", re.MULTILINE)


def run_rules(parsed: ParsedConfig, rules: Iterable[Rule] = RULES) -> dict:
    rules = tuple(rules)
    declared = bool(OSPF_DECLARED_RE.search(parsed.raw_text))
    if not declared:
        # No OSPF process at all: these rules do not apply. Do not call that "satisfied".
        return {"rules_checked": len(rules), "rules_satisfied": 0, "rules_not_applicable": len(rules),
                "findings_violated": 0, "findings_undetermined": 0, "findings": []}

    if parsed.ospf is None:
        # The text declares OSPF but the process block could not be parsed.
        reason = "配置声明了 OSPF，但进程块未被识别；无法确认下列规则的前提。"
        router_lines = [line for line in parsed.unparsed if re.match(r"router\s+ospf", line.text, re.IGNORECASE)]
        evidence = tuple(Evidence("config_line", line.number, line.text) for line in router_lines[:3])
        findings = [Finding(rule.id, "undetermined", reason, evidence) for rule in rules]
        return {"rules_checked": len(rules), "rules_satisfied": 0, "rules_not_applicable": 0,
                "findings_violated": 0, "findings_undetermined": len(findings), "findings": findings}

    findings: list[Finding] = []
    checked = 0
    for rule in rules:
        checked += 1
        findings.extend(rule.check(parsed))

    # Any OSPF-related line we could not parse means no rule may claim "satisfied".
    uncertain = _undetermined(parsed, "R000", "unused")
    if uncertain is not None:
        covered = {item.rule_id for item in findings}
        for rule in rules:
            if rule.id not in covered:
                reason = _undetermined(parsed, rule.id, "存在未解析的 OSPF 相关配置，无法确认本条规则。")
                if reason is not None:
                    findings.append(reason)
    violated = sum(1 for item in findings if item.status == "violated")
    undetermined = sum(1 for item in findings if item.status == "undetermined")
    rules_with_findings = len({item.rule_id for item in findings})
    return {
        "rules_checked": checked,
        "rules_satisfied": checked - rules_with_findings,
        "rules_not_applicable": 0,
        "findings_violated": violated,
        "findings_undetermined": undetermined,
        "findings": findings,
    }


def rules_payload() -> list[dict]:
    return [{"id": rule.id, "title": rule.title, "rationale": rule.rationale,
             "source": rule.source, "severity": rule.severity} for rule in RULES]

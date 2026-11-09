"""Deterministic parser for a declared subset of FRR configuration.

Design rules:
- Never guess. Unrecognised lines are recorded with their line numbers,
  they are not silently dropped.
- Every parsed value keeps the source line numbers so a later finding can
  point back to the exact evidence.
- Two hashes: raw (byte identity) and canonical (order/whitespace-insensitive).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

PARSER_VERSION = "frr-subset-2"

INTERFACE_RE = re.compile(r"^interface\s+(\S+)$")
ROUTER_OSPF_RE = re.compile(r"^router\s+ospf$")
HOSTNAME_RE = re.compile(r"^hostname\s+(\S+)$")
DESCRIPTION_RE = re.compile(r"^description\s+(.+)$")
IP_ADDRESS_RE = re.compile(r"^ip\s+address\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})$")
IP_OSPF_COST_RE = re.compile(r"^ip\s+ospf\s+cost\s+(\d{1,5})$")
IP_OSPF_AREA_RE = re.compile(r"^ip\s+ospf\s+area\s+(\S+)$")
IP_OSPF_NETWORK_RE = re.compile(r"^ip\s+ospf\s+network\s+(\S+)$")
IP_OSPF_PASSIVE_RE = re.compile(r"^ip\s+ospf\s+passive$")
NO_IP_OSPF_COST_RE = re.compile(r"^no\s+ip\s+ospf\s+cost$")
NO_IP_OSPF_AREA_RE = re.compile(r"^no\s+ip\s+ospf\s+area(?:\s+\S+)?$")
NO_IP_OSPF_NETWORK_RE = re.compile(r"^no\s+ip\s+ospf\s+network(?:\s+\S+)?$")
NO_IP_OSPF_PASSIVE_RE = re.compile(r"^no\s+ip\s+ospf\s+passive$")
NO_IP_ADDRESS_RE = re.compile(r"^no\s+ip\s+address\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})$")
NO_SHUTDOWN_RE = re.compile(r"^no\s+shutdown$")
SHUTDOWN_RE = re.compile(r"^shutdown$")
ROUTER_ID_RE = re.compile(r"^ospf\s+router-id\s+(\d{1,3}(?:\.\d{1,3}){3})$")
NETWORK_RE = re.compile(r"^network\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\s+area\s+(\S+)$")
PASSIVE_INTERFACE_RE = re.compile(r"^passive-interface\s+(\S+)$")
TIMERS_RE = re.compile(r"^timers\s+throttle\s+spf\s+.+$")
LOG_ADJACENCY_RE = re.compile(r"^log-adjacency-changes(?:\s+detail)?$")
DEFAULT_INFORMATION_RE = re.compile(r"^default-information\s+originate$")

# Configuration-level statements that are accepted without being modelled.
IGNORED_TOP_LEVEL_RE = re.compile(
    r"^(?:frr\s+defaults\s+\S+|log\s+(?:stdout|syslog(?:\s+.*)?|file\s+.*)|"
    r"service\s+integrated-vtysh-config|line\s+vty|"
    r"debug\s+.*|username\s+.*|password\s+.*|enable\s+password\s+.*|"
    r"ip\s+forwarding|version\s+\S+)$"
)
IGNORED_BLOCK_COMMAND_RE = re.compile(r"^(?:exit|end)$")

_BLOCK_TERMINATORS = {"!"}


@dataclass(frozen=True)
class SourceLine:
    number: int
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"line": self.number, "text": self.text}


@dataclass
class OspfInterface:
    area: str | None = None
    cost: int | None = None
    network_type: str | None = None
    passive: bool | None = None
    lines: dict[str, int] = field(default_factory=dict)

    def canonical(self) -> dict[str, Any]:
        return {
            "area": self.area,
            "cost": self.cost,
            "network_type": self.network_type,
            "passive": self.passive,
        }


@dataclass
class Interface:
    name: str
    description: str | None = None
    addresses: list[str] = field(default_factory=list)
    address_lines: list[int] = field(default_factory=list)
    shutdown: bool = False
    ospf: OspfInterface = field(default_factory=OspfInterface)
    start_line: int = 0
    end_line: int = 0

    def canonical(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "addresses": list(self.addresses),
            "shutdown": self.shutdown,
            "ospf": self.ospf.canonical(),
        }


@dataclass
class OspfNetwork:
    prefix: str
    area: str
    line: int

    def canonical(self) -> dict[str, Any]:
        return {"prefix": self.prefix, "area": self.area}


@dataclass
class OspfProcess:
    router_id: str | None = None
    networks: list[OspfNetwork] = field(default_factory=list)
    passive_interfaces: set[str] = field(default_factory=set)
    passive_default: bool = False
    default_information: bool | None = None
    start_line: int = 0
    end_line: int = 0
    lines: dict[str, int] = field(default_factory=dict)

    def canonical(self) -> dict[str, Any]:
        return {
            "router_id": self.router_id,
            "networks": sorted((n.canonical() for n in self.networks), key=lambda n: (n["prefix"], n["area"])),
            "passive_interfaces": sorted(self.passive_interfaces),
            "passive_default": self.passive_default,
            "default_information": self.default_information,
        }


@dataclass
class ParsedConfig:
    source_name: str
    raw_text: str
    raw_sha256: str
    canonical_sha256: str
    hostname: str | None
    hostname_line: int | None
    interfaces: dict[str, Interface]
    ospf: OspfProcess | None
    unparsed: list[SourceLine]
    line_count: int

    def canonical(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "interfaces": {name: self.interfaces[name].canonical() for name in sorted(self.interfaces)},
            "ospf": self.ospf.canonical() if self.ospf else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.canonical(), ensure_ascii=False, sort_keys=True, indent=2)


class ConfigParseError(ValueError):
    """Raised only for unusable input, never for an unknown-but-readable line."""


def canonical_hash(canonical: dict[str, Any]) -> str:
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def raw_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_frr_config(text: str, source_name: str = "config") -> ParsedConfig:
    if not isinstance(text, str):
        raise ConfigParseError("配置内容必须是文本")
    if "\x00" in text:
        raise ConfigParseError("配置内容包含 NUL 字节")
    lines = text.splitlines()
    if len(lines) > 20000:
        raise ConfigParseError("配置文件行数超出上限")

    hostname: str | None = None
    hostname_line: int | None = None
    interfaces: dict[str, Interface] = {}
    ospf: OspfProcess | None = None
    unparsed: list[SourceLine] = []

    block: str | None = None  # "interface" | "ospf" | "ignored"
    current_interface: Interface | None = None

    for index, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped in _BLOCK_TERMINATORS:
            if block == "interface" and current_interface is not None:
                current_interface.end_line = index
            if block == "ospf" and ospf is not None:
                ospf.end_line = index
            block = None
            current_interface = None
            continue
        if stripped.startswith("!"):
            continue  # comment

        if match := INTERFACE_RE.match(stripped):
            name = match.group(1)
            if block == "interface" and current_interface is not None:
                current_interface.end_line = index - 1
            if name in interfaces:
                # Re-entrant block: apply its commands in order to the same interface.
                current_interface = interfaces[name]
                block = "interface"
                continue
            current_interface = Interface(name=name, start_line=index)
            interfaces[name] = current_interface
            block = "interface"
            continue

        if ROUTER_OSPF_RE.match(stripped):
            if ospf is not None:
                block = "ospf"
                continue
            ospf = OspfProcess(start_line=index)
            block = "ospf"
            continue

        if block == "interface" and current_interface is not None:
            if _parse_interface_line(stripped, index, current_interface):
                continue
            if IGNORED_BLOCK_COMMAND_RE.match(stripped):
                continue
            unparsed.append(SourceLine(index, stripped))
            continue

        if block == "ospf" and ospf is not None:
            if _parse_ospf_line(stripped, index, ospf):
                continue
            if IGNORED_BLOCK_COMMAND_RE.match(stripped):
                continue
            unparsed.append(SourceLine(index, stripped))
            continue

        if block == "ignored":
            continue

        if match := HOSTNAME_RE.match(stripped):
            hostname = match.group(1)
            hostname_line = index
            continue
        if IGNORED_TOP_LEVEL_RE.match(stripped):
            if stripped.startswith("line vty"):
                block = "ignored"
            continue
        unparsed.append(SourceLine(index, stripped))

    canonical = {
        "hostname": hostname,
        "interfaces": {name: interfaces[name].canonical() for name in sorted(interfaces)},
        "ospf": ospf.canonical() if ospf else None,
    }
    return ParsedConfig(
        source_name=source_name,
        raw_text=text,
        raw_sha256=raw_hash(text),
        canonical_sha256=canonical_hash(canonical),
        hostname=hostname,
        hostname_line=hostname_line,
        interfaces=interfaces,
        ospf=ospf,
        unparsed=unparsed,
        line_count=len(lines),
    )


def _parse_interface_line(stripped: str, index: int, interface: Interface) -> bool:
    if match := DESCRIPTION_RE.match(stripped):
        interface.description = match.group(1).strip()
        return True
    if match := IP_ADDRESS_RE.match(stripped):
        address = match.group(1)
        if address not in interface.addresses:
            interface.addresses.append(address)
            interface.address_lines.append(index)
        interface.ospf.lines.setdefault("address", index)
        return True
    if match := NO_IP_ADDRESS_RE.match(stripped):
        address = match.group(1)
        if address in interface.addresses:
            position = interface.addresses.index(address)
            interface.addresses.pop(position)
            interface.address_lines.pop(position)
            interface.ospf.lines["address_removed"] = index
        return True
    if NO_IP_OSPF_COST_RE.match(stripped):
        interface.ospf.cost = None
        interface.ospf.lines["cost"] = index
        return True
    if NO_IP_OSPF_AREA_RE.match(stripped):
        interface.ospf.area = None
        interface.ospf.lines["area"] = index
        return True
    if NO_IP_OSPF_NETWORK_RE.match(stripped):
        interface.ospf.network_type = None
        interface.ospf.lines["network_type"] = index
        return True
    if NO_IP_OSPF_PASSIVE_RE.match(stripped):
        interface.ospf.passive = None
        interface.ospf.lines["passive"] = index
        return True
    if NO_SHUTDOWN_RE.match(stripped):
        interface.shutdown = False
        interface.ospf.lines["shutdown"] = index
        return True
    if match := IP_OSPF_COST_RE.match(stripped):
        interface.ospf.cost = int(match.group(1))
        interface.ospf.lines["cost"] = index
        return True
    if match := IP_OSPF_AREA_RE.match(stripped):
        interface.ospf.area = match.group(1)
        interface.ospf.lines["area"] = index
        return True
    if match := IP_OSPF_NETWORK_RE.match(stripped):
        interface.ospf.network_type = match.group(1)
        interface.ospf.lines["network_type"] = index
        return True
    if IP_OSPF_PASSIVE_RE.match(stripped):
        interface.ospf.passive = True
        interface.ospf.lines["passive"] = index
        return True
    if SHUTDOWN_RE.match(stripped):
        interface.shutdown = True
        interface.ospf.lines["shutdown"] = index
        return True
    return False


def _parse_ospf_line(stripped: str, index: int, ospf: OspfProcess) -> bool:
    if match := ROUTER_ID_RE.match(stripped):
        ospf.router_id = match.group(1)
        ospf.lines["router_id"] = index
        return True
    if match := NETWORK_RE.match(stripped):
        ospf.networks.append(OspfNetwork(prefix=match.group(1), area=match.group(2), line=index))
        return True
    if match := PASSIVE_INTERFACE_RE.match(stripped):
        name = match.group(1)
        if name == "default":
            ospf.passive_default = True
        else:
            ospf.passive_interfaces.add(name)
        ospf.lines.setdefault("passive_interfaces", index)
        return True
    if DEFAULT_INFORMATION_RE.match(stripped):
        ospf.default_information = True
        return True
    if TIMERS_RE.match(stripped) or LOG_ADJACENCY_RE.match(stripped):
        return True
    return False

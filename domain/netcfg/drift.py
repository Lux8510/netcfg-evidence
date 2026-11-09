"""Structured, evidence-carrying diff between two parsed configurations.

Every drift item points at the line numbers on both sides. Nothing is
inferred: a change is reported only when the canonical value differs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .parser import Interface, ParsedConfig


@dataclass(frozen=True)
class DriftItem:
    path: str
    category: str  # hostname | interface | ospf
    kind: str  # added | removed | changed
    old: Any = None
    new: Any = None
    old_lines: tuple[int, ...] = ()
    new_lines: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "category": self.category,
            "kind": self.kind,
            "old": self.old,
            "new": self.new,
            "old_lines": list(self.old_lines),
            "new_lines": list(self.new_lines),
        }


@dataclass(frozen=True)
class DriftReport:
    baseline_label: str
    current_label: str
    baseline_hash: str
    current_hash: str
    items: tuple[DriftItem, ...]

    @property
    def identical(self) -> bool:
        return not self.items

    def summary(self) -> dict[str, int]:
        counts = {"added": 0, "removed": 0, "changed": 0}
        for item in self.items:
            counts[item.kind] += 1
        counts["total"] = len(self.items)
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline_label,
            "current": self.current_label,
            "baseline_hash": self.baseline_hash,
            "current_hash": self.current_hash,
            "identical": self.identical,
            "summary": self.summary(),
            "items": [item.as_dict() for item in self.items],
        }


def _line(number: int | None) -> tuple[int, ...]:
    return (number,) if number else ()


def _address_evidence(old: Interface, new: Interface) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Point at the addresses that actually differ, not always the first one."""
    old_lines: list[int] = []
    new_lines: list[int] = []
    for index in range(max(len(old.addresses), len(new.addresses))):
        old_value = old.addresses[index] if index < len(old.addresses) else None
        new_value = new.addresses[index] if index < len(new.addresses) else None
        if old_value == new_value:
            continue
        if index < len(old.address_lines):
            old_lines.append(old.address_lines[index])
        if index < len(new.address_lines):
            new_lines.append(new.address_lines[index])
    return tuple(old_lines or _block_lines(old)), tuple(new_lines or _block_lines(new))


def _block_lines(interface: Interface) -> tuple[int, ...]:
    if interface.start_line and interface.end_line:
        return tuple(range(interface.start_line, interface.end_line + 1))
    if interface.start_line:
        return (interface.start_line,)
    return ()


def _field_lines(interface: Interface, field_name: str) -> tuple[int, ...]:
    line = interface.ospf.lines.get(field_name)
    return (line,) if line else _block_lines(interface)


def diff_configs(baseline: ParsedConfig, current: ParsedConfig) -> DriftReport:
    items: list[DriftItem] = []

    if baseline.hostname != current.hostname:
        items.append(DriftItem("hostname", "hostname", "changed", baseline.hostname, current.hostname,
                               _line(baseline.hostname_line), _line(current.hostname_line)))

    base_names = set(baseline.interfaces)
    cur_names = set(current.interfaces)
    for name in sorted(base_names - cur_names):
        interface = baseline.interfaces[name]
        items.append(DriftItem(f"interface.{name}", "interface", "removed",
                               interface.canonical(), None, _block_lines(interface), ()))
    for name in sorted(cur_names - base_names):
        interface = current.interfaces[name]
        items.append(DriftItem(f"interface.{name}", "interface", "added",
                               None, interface.canonical(), (), _block_lines(interface)))

    for name in sorted(base_names & cur_names):
        old = baseline.interfaces[name]
        new = current.interfaces[name]
        _compare_field(items, name, "description", old.description, new.description,
                       _line(old.description_line) or _block_lines(old),
                       _line(new.description_line) or _block_lines(new))
        if old.addresses != new.addresses:
            old_lines, new_lines = _address_evidence(old, new)
            items.append(DriftItem(f"interface.{name}.addresses", "interface", "changed",
                                   list(old.addresses), list(new.addresses), old_lines, new_lines))
        _compare_field(items, name, "shutdown", old.shutdown, new.shutdown,
                       _block_lines(old), _block_lines(new))
        _compare_field(items, name, "ospf.area", old.ospf.area, new.ospf.area,
                       _field_lines(old, "area"), _field_lines(new, "area"))
        _compare_field(items, name, "ospf.cost", old.ospf.cost, new.ospf.cost,
                       _field_lines(old, "cost"), _field_lines(new, "cost"))
        _compare_field(items, name, "ospf.network_type", old.ospf.network_type, new.ospf.network_type,
                       _field_lines(old, "network_type"), _field_lines(new, "network_type"))
        _compare_field(items, name, "ospf.passive", old.ospf.passive, new.ospf.passive,
                       _field_lines(old, "passive"), _field_lines(new, "passive"))

    old_ospf = baseline.ospf
    new_ospf = current.ospf
    if (old_ospf is None) != (new_ospf is None):
        items.append(DriftItem("ospf", "ospf", "added" if old_ospf is None else "removed",
                               old_ospf.canonical() if old_ospf else None,
                               new_ospf.canonical() if new_ospf else None,
                               _ospf_lines(old_ospf), _ospf_lines(new_ospf)))
    elif old_ospf is not None and new_ospf is not None:
        _compare_field(items, None, "ospf.router_id", old_ospf.router_id, new_ospf.router_id,
                       _ospf_field_lines(old_ospf, "router_id"), _ospf_field_lines(new_ospf, "router_id"))
        old_networks = sorted((n.prefix, n.area) for n in old_ospf.networks)
        new_networks = sorted((n.prefix, n.area) for n in new_ospf.networks)
        if old_networks != new_networks:
            items.append(DriftItem("ospf.networks", "ospf", "changed",
                                   [{"prefix": p, "area": a} for p, a in old_networks],
                                   [{"prefix": p, "area": a} for p, a in new_networks],
                                   tuple(n.line for n in old_ospf.networks),
                                   tuple(n.line for n in new_ospf.networks)))
        if sorted(old_ospf.passive_interfaces) != sorted(new_ospf.passive_interfaces):
            items.append(DriftItem("ospf.passive_interfaces", "ospf", "changed",
                                   sorted(old_ospf.passive_interfaces), sorted(new_ospf.passive_interfaces),
                                   _ospf_field_lines(old_ospf, "passive_interfaces"),
                                   _ospf_field_lines(new_ospf, "passive_interfaces")))
        _compare_field(items, None, "ospf.passive_default", old_ospf.passive_default, new_ospf.passive_default,
                       _ospf_field_lines(old_ospf, "passive_interfaces"), _ospf_field_lines(new_ospf, "passive_interfaces"))
        _compare_field(items, None, "ospf.default_information", old_ospf.default_information, new_ospf.default_information,
                       _ospf_field_lines(old_ospf, "default_information"), _ospf_field_lines(new_ospf, "default_information"))

    return DriftReport(
        baseline_label=baseline.source_name,
        current_label=current.source_name,
        baseline_hash=baseline.canonical_sha256,
        current_hash=current.canonical_sha256,
        items=tuple(items),
    )


def _compare_field(items: list[DriftItem], interface_name: str | None, field_name: str,
                   old: Any, new: Any, old_lines: tuple[int, ...], new_lines: tuple[int, ...]) -> None:
    if old == new:
        return
    path = f"interface.{interface_name}.{field_name}" if interface_name else field_name
    items.append(DriftItem(path, "interface" if interface_name else "ospf", "changed", old, new, old_lines, new_lines))


def _ospf_lines(ospf) -> tuple[int, ...]:
    if ospf is None:
        return ()
    if ospf.start_line and ospf.end_line:
        return tuple(range(ospf.start_line, ospf.end_line + 1))
    return (ospf.start_line,) if ospf.start_line else ()


def _ospf_field_lines(ospf, field_name: str) -> tuple[int, ...]:
    line = ospf.lines.get(field_name)
    return (line,) if line else _ospf_lines(ospf)

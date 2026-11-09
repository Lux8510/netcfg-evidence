"""Rules that need two linked devices. They never guess: missing data means undetermined."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from .parser import ParsedConfig
from .rules import Evidence, Finding

LINK_MISMATCH = "R005"
DUPLICATE_IP = "R006"
SUBNET_MISMATCH = "R007"


@dataclass(frozen=True)
class Peer:
    device_id: str
    device_name: str
    version_id: str
    interface: str
    parsed: ParsedConfig


def effective_area(parsed: ParsedConfig, interface_name: str) -> tuple[str | None, int | None]:
    """Interface-level area wins; otherwise the area of the network statement covering it."""
    interface = parsed.interfaces.get(interface_name)
    if interface is None:
        return None, None
    if interface.ospf.area:
        return interface.ospf.area, interface.ospf.lines.get("area")
    if parsed.ospf is None:
        return None, None
    for address in interface.addresses:
        try:
            network = ipaddress.ip_interface(address).network
        except ValueError:
            continue
        for statement in parsed.ospf.networks:
            try:
                if ipaddress.ip_network(statement.prefix, strict=False) == network:
                    return statement.area, statement.line
            except ValueError:
                continue
    return None, None


def _addresses(parsed: ParsedConfig, interface_name: str) -> list[tuple[str, int | None]]:
    interface = parsed.interfaces.get(interface_name)
    if interface is None:
        return []
    return list(zip(interface.addresses, interface.address_lines or [None] * len(interface.addresses)))


def check_link(local: ParsedConfig, local_version: str, local_iface: str,
               peer: Peer) -> list[Finding]:
    findings: list[Finding] = []
    local_iface_obj = local.interfaces.get(local_iface)
    peer_iface_obj = peer.parsed.interfaces.get(peer.interface)
    if local_iface_obj is None or peer_iface_obj is None:
        return [Finding(
            LINK_MISMATCH, "undetermined",
            f"链路一端缺少接口：本端 {local_iface}，对端 {peer.device_name}/{peer.interface}。",
            (Evidence("parsed_value", None, "interface missing", f"interface.{local_iface}",
                      local_version),),
        )]

    local_area, local_area_line = effective_area(local, local_iface)
    peer_area, peer_area_line = effective_area(peer.parsed, peer.interface)
    if local_area and peer_area and local_area != peer_area:
        findings.append(Finding(
            LINK_MISMATCH, "violated",
            f"链路两端 OSPF 区域不一致：{local_iface} 属于 {local_area}，"
            f"{peer.device_name}/{peer.interface} 属于 {peer_area}。",
            (Evidence("config_line", local_area_line, f"area {local_area}", f"interface.{local_iface}.ospf.area",
                      local_version),
             Evidence("config_line", peer_area_line, f"area {peer_area}",
                      f"interface.{peer.interface}.ospf.area", peer.version_id)),
        ))
    elif local_area is None or peer_area is None:
        missing = "本端" if local_area is None else "对端"
        findings.append(Finding(
            LINK_MISMATCH, "undetermined",
            f"无法确认链路两端区域：{missing}接口没有可解析的 OSPF area。",
            (Evidence("parsed_value", None, "area missing", f"interface.{local_iface}.ospf.area",
                      local_version),),
        ))

    local_addresses = _addresses(local, local_iface)
    peer_addresses = _addresses(peer.parsed, peer.interface)
    if local_addresses and peer_addresses:
        local_nets = {ipaddress.ip_interface(a).network for a, _ in local_addresses if _valid(a)}
        peer_nets = {ipaddress.ip_interface(a).network for a, _ in peer_addresses if _valid(a)}
        if local_nets and peer_nets and not (local_nets & peer_nets):
            findings.append(Finding(
                SUBNET_MISMATCH, "violated",
                f"链路两端地址不在同一网段：{local_iface} {local_addresses[0][0]} 与 "
                f"{peer.device_name}/{peer.interface} {peer_addresses[0][0]}。",
                (Evidence("config_line", local_addresses[0][1], local_addresses[0][0],
                          f"interface.{local_iface}.addresses", local_version),
                 Evidence("config_line", peer_addresses[0][1], peer_addresses[0][0],
                          f"interface.{peer.interface}.addresses", peer.version_id)),
            ))
    return findings


def _valid(address: str) -> bool:
    try:
        ipaddress.ip_interface(address)
        return True
    except ValueError:
        return False


def check_duplicate_addresses(local: ParsedConfig, local_version: str,
                              peers: list[Peer]) -> list[Finding]:
    """Only report pairs that involve the local device, so each duplicate is reported once per side."""
    findings: list[Finding] = []
    local_entries = [(name, address, line) for name, interface in local.interfaces.items()
                     for address, line in _addresses(local, name)]
    for peer in peers:
        peer_entries = [(name, address, line) for name, interface in peer.parsed.interfaces.items()
                        for address, line in _addresses(peer.parsed, name)]
        for local_name, local_address, local_line in local_entries:
            for peer_name, peer_address, peer_line in peer_entries:
                if local_address != peer_address:
                    continue
                findings.append(Finding(
                    DUPLICATE_IP, "violated",
                    f"地址 {local_address} 同时出现在本机 {local_name} 与 "
                    f"{peer.device_name}/{peer_name}。",
                    (Evidence("config_line", local_line, local_address,
                              f"interface.{local_name}.addresses", local_version),
                     Evidence("config_line", peer_line, peer_address,
                              f"interface.{peer_name}.addresses", peer.version_id)),
                ))
    return findings


CROSS_RULES = (
    (LINK_MISMATCH, "链路两端 OSPF area 必须一致"),
    (DUPLICATE_IP, "同一地址不能出现在两台设备上"),
    (SUBNET_MISMATCH, "链路两端地址必须在同一网段"),
)

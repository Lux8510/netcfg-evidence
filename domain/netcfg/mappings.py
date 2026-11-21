"""Explicit (environment, topology, node) -> device bindings.

No inference from device names: an import must name its environment/topology
and resolve the node through an active mapping.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.models import Device, DeviceMapping, uid

from .service import ServiceError, _device


def _text(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 80:
        raise ServiceError("INVALID_MAPPING", f"{field} 需要 1–80 个字符", 422)
    return value


def create_mapping(db: Session, *, environment: str, topology: str, node: str,
                   device_id: str, actor: str) -> DeviceMapping:
    environment = _text(environment, "环境")
    topology = _text(topology, "拓扑")
    node = _text(node, "节点")
    actor = (actor or "").strip()
    if not actor or len(actor) > 80:
        raise ServiceError("INVALID_ACTOR", "登记人无效", 422)
    _device(db, device_id)
    existing = db.scalar(select(DeviceMapping).where(
        DeviceMapping.environment == environment,
        DeviceMapping.topology == topology,
        DeviceMapping.node == node,
        DeviceMapping.active.is_(True)))
    if existing is not None:
        raise ServiceError("MAPPING_EXISTS",
                           f"{environment}/{topology}/{node} 已绑定到设备 {existing.device_id}", 409)
    mapping = DeviceMapping(id=uid(), environment=environment, topology=topology, node=node,
                            device_id=device_id, active=True, created_by=actor)
    try:
        with db.begin_nested():
            db.add(mapping)
            db.flush()
    except IntegrityError:
        raise ServiceError("MAPPING_EXISTS", "同一环境/拓扑/节点已存在有效绑定", 409) from None
    return mapping


def resolve_mapping(db: Session, environment: str, topology: str, node: str) -> DeviceMapping | None:
    return db.scalar(select(DeviceMapping).where(
        DeviceMapping.environment == environment,
        DeviceMapping.topology == topology,
        DeviceMapping.node == node,
        DeviceMapping.active.is_(True)))


def list_mappings(db: Session, *, environment: str | None = None,
                  topology: str | None = None, device_id: str | None = None) -> list[DeviceMapping]:
    query = select(DeviceMapping).where(DeviceMapping.active.is_(True))
    if environment:
        query = query.where(DeviceMapping.environment == environment)
    if topology:
        query = query.where(DeviceMapping.topology == topology)
    if device_id:
        _device(db, device_id)
        query = query.where(DeviceMapping.device_id == device_id)
    return list(db.scalars(query.order_by(DeviceMapping.environment, DeviceMapping.topology,
                                          DeviceMapping.node)))


def deactivate_mapping(db: Session, mapping_id: str, actor: str) -> DeviceMapping:
    mapping = db.get(DeviceMapping, mapping_id)
    if mapping is None or not mapping.active:
        raise ServiceError("MAPPING_NOT_FOUND", "有效映射不存在", 404)
    mapping.active = False
    mapping.deactivated_by = (actor or "").strip()[:80] or "unknown"
    mapping.deactivated_at = datetime.now(timezone.utc)
    db.flush()
    return mapping


def payload(db: Session, mapping: DeviceMapping) -> dict[str, Any]:
    device = db.get(Device, mapping.device_id)
    return {
        "id": mapping.id,
        "environment": mapping.environment,
        "topology": mapping.topology,
        "node": mapping.node,
        "device_id": mapping.device_id,
        "device_name": device.name if device else mapping.device_id,
        "active": mapping.active,
        "created_by": mapping.created_by,
        "created_at": mapping.created_at.isoformat(),
        "deactivated_by": mapping.deactivated_by,
        "deactivated_at": mapping.deactivated_at.isoformat() if mapping.deactivated_at else None,
    }

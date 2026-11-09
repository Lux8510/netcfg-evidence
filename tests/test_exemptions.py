"""Time-boxed drift exemptions: value-bound, expiry-aware, revocable, audited."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from domain.netcfg import exemptions, service
from domain.netcfg.change_requests import validate_actor
from engine.db import Base, make_engine
from engine.models import AuditEvent, DriftExemption

ROOT = Path(__file__).resolve().parents[1]
BASELINE = (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8")
@pytest.fixture()
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'exempt.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def seed(factory, new_cost: str = "20"):
    sessions, engine = factory
    with sessions() as db:
        device = service.create_device(db, "r1")
        version = service.import_config(db, device.id, BASELINE, "baseline")[0]
        service.set_baseline(db, device.id, version.id)
        service.import_config(db, device.id, BASELINE.replace(" ip ospf cost 10", f" ip ospf cost {new_cost}"), "changed")
        report = service.compute_drift(db, device.id)
        db.commit()
        return device.id, report.id


def make(factory, device_id, report_id, actor="何深", days=7, reason="已知偏差，等待下个维护窗口统一整改"):
    sessions, _ = factory
    with sessions() as db:
        row = exemptions.create_exemption(db, device_id, report_id, "interface.eth2.ospf.cost",
                                          reason, actor, datetime.now(timezone.utc) + timedelta(days=days))
        db.commit()
        return row.id


def test_exemption_matches_only_its_value(factory):
    sessions, _ = factory
    device_id, report_id = seed(factory, "20")
    make(factory, device_id, report_id)
    with sessions() as db:
        report = service.list_drifts(db, device_id)[0]
        payload = exemptions.decorate_report(db, report)
        assert payload["summary"]["exempted"] == 1
        assert payload["summary"]["actionable"] == payload["summary"]["total"] - 1
        assert payload["items"][0]["exemption"]["path"] == "interface.eth2.ospf.cost"

    # A different value on the same path is a new drift, not covered by the old exemption.
    with sessions() as db:
        device = db.get(service.Device, device_id)
        service.import_config(db, device.id, BASELINE.replace(" ip ospf cost 10", " ip ospf cost 40"), "changed-again")
        report = service.compute_drift(db, device.id)
        payload = exemptions.decorate_report(db, report)
        assert payload["summary"]["exempted"] == 0
        db.commit()


def test_expired_exemption_stops_applying(factory):
    sessions, _ = factory
    device_id, report_id = seed(factory)
    exemption_id = make(factory, device_id, report_id)
    with sessions() as db:
        db.execute(update(DriftExemption).where(DriftExemption.id == exemption_id).values(
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        db.commit()
        report = service.list_drifts(db, device_id)[0]
        payload = exemptions.decorate_report(db, report)
        assert payload["summary"]["exempted"] == 0
        listed = exemptions.list_exemptions(db, device_id)
        assert exemptions.payload(listed[0])["expired"] is True


def test_revoked_exemption_stops_applying_and_is_audited(factory):
    sessions, _ = factory
    device_id, report_id = seed(factory)
    exemption_id = make(factory, device_id, report_id)
    with sessions() as db:
        exemptions.revoke_exemption(db, exemption_id, "主管", "偏差已整改")
        db.commit()
    with sessions() as db:
        report = service.list_drifts(db, device_id)[0]
        assert exemptions.decorate_report(db, report)["summary"]["exempted"] == 0
        actions = [row.action for row in db.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == "drift_exemption"))]
        assert actions == ["created", "revoked"]


def test_validation_rules(factory):
    sessions, _ = factory
    device_id, report_id = seed(factory)
    with sessions() as db:
        with pytest.raises(service.ServiceError) as excinfo:
            exemptions.create_exemption(db, device_id, report_id, "interface.nope", "x" * 12,
                                        "何深", datetime.now(timezone.utc) + timedelta(days=1))
        assert excinfo.value.code == "DRIFT_PATH_NOT_FOUND"

        with pytest.raises(service.ServiceError) as excinfo:
            exemptions.create_exemption(db, device_id, report_id, "interface.eth2.ospf.cost", "short",
                                        "何深", datetime.now(timezone.utc) + timedelta(days=1))
        assert excinfo.value.code == "INVALID_REASON"

        with pytest.raises(service.ServiceError) as excinfo:
            exemptions.create_exemption(db, device_id, report_id, "interface.eth2.ospf.cost", "已知偏差，等待下个维护窗口统一整改",
                                        "何深", datetime.now(timezone.utc) - timedelta(days=1))
        assert excinfo.value.code == "INVALID_EXPIRY"

        with pytest.raises(service.ServiceError) as excinfo:
            exemptions.create_exemption(db, device_id, report_id, "interface.eth2.ospf.cost", "已知偏差，等待下个维护窗口统一整改",
                                        "何深", datetime.now(timezone.utc) + timedelta(days=400))
        assert excinfo.value.code == "INVALID_EXPIRY"

        with pytest.raises(service.ServiceError) as excinfo:
            exemptions.create_exemption(db, device_id, report_id, "interface.eth2.ospf.cost", "已知偏差，等待下个维护窗口统一整改",
                                        "ＡＧＥＮＴ", datetime.now(timezone.utc) + timedelta(days=1))
        assert excinfo.value.code == "HUMAN_APPROVAL_REQUIRED"


def test_duplicate_active_exemption_is_rejected(factory):
    sessions, _ = factory
    device_id, report_id = seed(factory)
    make(factory, device_id, report_id)
    with sessions() as db:
        with pytest.raises(service.ServiceError) as excinfo:
            exemptions.create_exemption(db, device_id, report_id, "interface.eth2.ospf.cost",
                                        "重复登记同一偏差用于测试", "主管",
                                        datetime.now(timezone.utc) + timedelta(days=1))
        assert excinfo.value.code == "EXEMPTION_EXISTS"


def test_api_flow_marks_items_and_creates_audit(app_client):
    def post(path, payload):
        response = app_client.post(path, json=payload)
        assert response.status_code < 400, response.text
        return response.json()["data"]

    device = post("/api/devices", {"name": "r1"})
    baseline = post(f"/api/devices/{device['id']}/configs", {"text": BASELINE, "source_name": "baseline"})
    post(f"/api/devices/{device['id']}/baseline", {"version_id": baseline["id"]})
    post(f"/api/devices/{device['id']}/configs",
         {"text": BASELINE.replace(" ip ospf cost 10", " ip ospf cost 20"), "source_name": "changed"})
    report = post(f"/api/devices/{device['id']}/drift", {})
    assert report["summary"]["exempted"] == 0

    exemption = post(f"/api/devices/{device['id']}/drift/exemptions", {
        "drift_report_id": report["id"],
        "path": "interface.eth2.ospf.cost",
        "reason": "已知偏差，等待下个维护窗口统一整改",
        "actor": "何深",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    })
    assert exemption["active"] is True

    decorated = app_client.get(f"/api/drift/{report['id']}").json()["data"]
    assert decorated["summary"]["exempted"] == 1
    assert decorated["items"][0]["exemption"]["id"] == exemption["id"]

    listed = app_client.get(f"/api/devices/{device['id']}/drift/exemptions").json()["data"]
    assert len(listed) == 1

    revoked = post(f"/api/exemptions/{exemption['id']}/revoke", {"actor": "主管", "note": "偏差已整改"})
    assert revoked["revoked_by"] == "主管"
    after = app_client.get(f"/api/drift/{report['id']}").json()["data"]
    assert after["summary"]["exempted"] == 0

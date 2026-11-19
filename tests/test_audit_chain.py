from __future__ import annotations

from sqlalchemy import text

from engine.audit_chain import verify_chain


def audit_flow(client, device_id):
    from conftest import SAMPLES, import_config

    text_ok = (SAMPLES / "r1.conf").read_text(encoding="utf-8")
    broken = text_ok.replace(" ip ospf cost 10\n", "").replace(" passive-interface lo\n", "")
    version = import_config(client, device_id, broken).json()["data"]
    result = client.post(f"/api/devices/{device_id}/rules/check", json={}).json()["data"]
    finding = result["findings"][0]
    response = client.post(f"/api/devices/{device_id}/change-requests", json={
        "title": "审计链测试变更单", "rationale": "用于生成审计事件并验证哈希链", "finding_ids": [finding["id"]],
        "proposed_commands": ["interface eth2", " ip ospf cost 10"],
    })
    assert response.status_code == 201, response.text
    change = response.json()["data"]
    for action in ("submit", "approve"):
        response = client.post(f"/api/change-requests/{change['id']}/{action}",
                               json={"note": action, "idempotency_key": f"chain-{action}-key"})
        assert response.status_code == 200, response.text
    return change


def test_audit_chain_verifies_after_approval(app_client, app_session):
    from conftest import create_device

    device = create_device(app_client, "chain-ok")
    audit_flow(app_client, device)
    result = app_client.get("/api/audit/verify").json()["data"]
    assert result["ok"] is True
    assert result["checked"] >= 2
    assert result["head"]


def test_chain_entries_link_previous_hash(app_client, app_session):
    from conftest import create_device
    from engine.models import AuditChainEntry

    device = create_device(app_client, "chain-links")
    audit_flow(app_client, device)
    with app_session() as db:
        rows = db.execute(text("SELECT seq, prev_hash, entry_hash FROM audit_chain_entries ORDER BY seq")).all()
    assert rows
    prev = "0" * 64
    for seq, prev_hash, digest in rows:
        assert prev_hash == prev
        assert len(digest) == 64
        prev = digest


def test_tampering_is_detected_at_the_first_bad_entry(app_client, app_session):
    from conftest import create_device
    from engine.models import AuditEvent

    device = create_device(app_client, "chain-tamper")
    audit_flow(app_client, device)
    with app_session() as db:
        event = db.query(AuditEvent).order_by(AuditEvent.created_at).first()
        db.execute(text("DROP TRIGGER IF EXISTS audit_events_no_update"))
        db.execute(text("UPDATE audit_events SET note = 'tampered' WHERE id = :id"), {"id": event.id})
        db.commit()
    with app_session() as db:
        result = verify_chain(db)
    assert result["ok"] is False
    assert result["first_bad_seq"] == 1
    assert "hash" in result["reason"]


def test_event_missing_from_chain_is_detected(app_client, app_session):
    from conftest import create_device
    from engine.models import AuditEvent

    device = create_device(app_client, "chain-missing")
    audit_flow(app_client, device)
    with app_session() as db:
        db.add(AuditEvent(device_id=device, entity_type="test", entity_id=device,
                          action="off_chain", actor="x", note="not in chain", snapshot={}))
        db.commit()
    with app_session() as db:
        result = verify_chain(db)
    assert result["ok"] is False
    assert "missing" in result["reason"]

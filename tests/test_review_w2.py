"""Regressions for the W2 review (2026-11-09, items 01-15)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

from domain.netcfg import change_requests as changes
from domain.netcfg import service
from domain.netcfg.explain import explain_run
from domain.netcfg.parser import parse_frr_config
from domain.netcfg.rules import run_rules
from engine.db import Base, make_engine
from engine.models import AuditEvent, ChangeRequest, Explanation, Finding

SAMPLES = Path(__file__).resolve().parents[1] / "samples/frr"


@pytest.fixture(scope="module")
def r1_text() -> str:
    return (SAMPLES / "r1.conf").read_text(encoding="utf-8")


@pytest.fixture()
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'w2.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def seed_request(factory, r1_text) -> tuple[str, str]:
    with factory() as db:
        device = service.create_device(db, "r1")
        service.import_config(db, device.id, r1_text.replace(" ip ospf cost 10\n", ""), "r1")
        run = service.run_rules_check(db, device.id)
        request = changes.create_change_request(
            db, device.id, title="补回 cost", rationale="R001 要求显式 cost，需要人工核对后修复。",
            finding_ids=[run["findings"][0]["id"]], proposed_commands=["interface eth2", " ip ospf cost 10"],
            actor="何深")
        changes.transition(db, request.id, "submit", actor="何深", note="提交审批")
        db.commit()
        return device.id, request.id


# [01] Two competing decisions: only one may win, and the loser must not leave an event.
def test_concurrent_approve_and_reject_leave_one_event(factory, r1_text):
    _, request_id = seed_request(factory, r1_text)
    with factory() as first:
        changes.transition(first, request_id, "approve", actor="主管甲", note="同意")
        first.commit()
    with factory() as second:
        with pytest.raises(service.ServiceError) as excinfo:
            changes.transition(second, request_id, "reject", actor="主管乙", note="反对")
        assert excinfo.value.code == "INVALID_TRANSITION"
        second.rollback()

    with factory() as db:
        assert db.get(ChangeRequest, request_id).status == "approved"
        actions = [row.action for row in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at))]
        assert actions == ["created", "submit", "approve"]


# [02] Full-width reserved names must not pass as humans.
@pytest.mark.parametrize("actor", ["ＡＧＥＮＴ", "ｓｙｓｔｅｍ", "  Agent  "])
def test_reserved_actor_names_are_normalised_before_check(factory, r1_text, actor):
    device_id, _ = seed_request(factory, r1_text)
    with factory() as db:
        run = service.run_rules_check(db, device_id)
        with pytest.raises(service.ServiceError) as excinfo:
            changes.create_change_request(
                db, device_id, title="测试标题", rationale="用于测试审批人规范化的最小理由。",
                finding_ids=[run["findings"][0]["id"]], proposed_commands=["x"], actor=actor)
        assert excinfo.value.code in {"HUMAN_APPROVAL_REQUIRED", "INVALID_ACTOR"}


# [03] Audit events are append-only in the database, not only in the HTTP layer.
def test_audit_events_cannot_be_updated_or_deleted(factory, r1_text):
    _, request_id = seed_request(factory, r1_text)
    with factory() as db:
        with pytest.raises(DatabaseError):
            db.execute(text("UPDATE audit_events SET note='amended' WHERE action='created'"))
            db.commit()
        db.rollback()
        with pytest.raises(DatabaseError):
            db.execute(text("DELETE FROM audit_events WHERE action='created'"))
            db.commit()
        db.rollback()
    with factory() as db:
        rows = list(db.scalars(select(AuditEvent)))
        assert rows and all(row.note != "amended" for row in rows)


# [05] A rejected explanation must not change the finding or its evidence.
def test_rejected_explanation_does_not_touch_the_finding(factory, r1_text):
    from domain.netcfg.explain import MockExplainer

    with factory() as db:
        device = service.create_device(db, "r1")
        service.import_config(db, device.id, r1_text.replace(" ip ospf cost 10\n", ""), "r1")
        run = service.run_rules_check(db, device.id)
        before = [dict(row.evidence[0]) for row in db.scalars(select(Finding))]
        rows = explain_run(db, device.id, run["run_id"], MockExplainer("wrong_line"))
        assert rows[0]["status"] == "rejected"
        db.commit()
    with factory() as db:
        finding = db.scalar(select(Finding))
        assert finding.status == "violated"
        assert [dict(finding.evidence[0])] == before
        assert db.scalar(select(Explanation)).status == "rejected"


# [08] A clean run is still the latest run, and it has no findings to explain.
def test_latest_rule_run_includes_zero_finding_runs(factory, r1_text):
    with factory() as db:
        device = service.create_device(db, "r1")
        service.import_config(db, device.id, r1_text.replace(" ip ospf cost 10\n", ""), "broken")
        first = service.run_rules_check(db, device.id)
        service.import_config(db, device.id, r1_text, "clean")
        second = service.run_rules_check(db, device.id)
        db.commit()

        runs = service.list_rule_runs(db, device.id)
        assert [run.id for run in runs] == [second["run_id"], first["run_id"]]
        assert runs[0].counts["findings_violated"] == 0
        with pytest.raises(service.ServiceError) as excinfo:
            explain_run(db, device.id, second["run_id"])
        assert excinfo.value.code == "NO_FINDINGS"


# [09] Drift payload must expose the baseline *version*, not just the baseline row id.
def test_drift_payload_exposes_baseline_version(app_client, r1_text):
    device_id = app_client.post("/api/devices", json={"name": "r1"}).json()["data"]["id"]
    baseline_version = app_client.post(f"/api/devices/{device_id}/configs",
                                       json={"text": r1_text, "source_name": "baseline"}).json()["data"]["id"]
    current_version = app_client.post(f"/api/devices/{device_id}/configs",
                                      json={"text": r1_text.replace(" ip ospf cost 10", " ip ospf cost 20"),
                                            "source_name": "current"}).json()["data"]["id"]
    app_client.post(f"/api/devices/{device_id}/baseline", json={"version_id": baseline_version})
    report = app_client.post(f"/api/devices/{device_id}/drift", json={}).json()["data"]
    assert report["baseline_version_id"] == baseline_version
    assert report["current_version_id"] == current_version

    item = report["items"][0]
    old = app_client.get(f"/api/versions/{report['baseline_version_id']}/lines",
                         params={"start": item["old_lines"][0], "end": item["old_lines"][0]}).json()["data"]
    new = app_client.get(f"/api/versions/{report['current_version_id']}/lines",
                         params={"start": item["new_lines"][0], "end": item["new_lines"][0]}).json()["data"]
    assert "cost 10" in old["lines"][0]["text"]
    assert "cost 20" in new["lines"][0]["text"]


# [10]/[11] Re-entrant blocks are applied in order, so a later `no ip ospf cost` wins.
def test_reentrant_block_negation_changes_the_rule_result():
    text = ("router ospf\n!\ninterface eth0\n ip ospf network point-to-point\n ip ospf cost 10\n!\n"
            "interface eth0\n no ip ospf cost\n!\n")
    result = run_rules(parse_frr_config(text))
    assert result["findings_violated"] == 1
    assert result["findings"][0].rule_id == "R001"


def test_reentrant_block_order_is_visible_in_canonical_form():
    set_then_unset = ("interface eth0\n ip ospf cost 10\n!\ninterface eth0\n no ip ospf cost\n!\n")
    unset_then_set = ("interface eth0\n no ip ospf cost\n!\ninterface eth0\n ip ospf cost 10\n!\n")
    assert parse_frr_config(set_then_unset).canonical_sha256 != parse_frr_config(unset_then_set).canonical_sha256


# [15] Docs and OpenAPI keep working next to the root static mount.
def test_docs_and_openapi_stay_available(app_client):
    assert app_client.get("/docs").status_code == 200
    schema = app_client.get("/openapi.json")
    assert schema.status_code == 200 and "application/json" in schema.headers["content-type"]


# [08 follow-up] Rule runs are exposed over HTTP, newest first.
def test_rule_runs_endpoint(app_client, r1_text):
    device_id = app_client.post("/api/devices", json={"name": "r1"}).json()["data"]["id"]
    app_client.post(f"/api/devices/{device_id}/configs", json={"text": r1_text, "source_name": "clean"})
    first = app_client.post(f"/api/devices/{device_id}/rules/check", json={}).json()["data"]
    second = app_client.post(f"/api/devices/{device_id}/rules/check", json={}).json()["data"]
    runs = app_client.get(f"/api/devices/{device_id}/rules/runs").json()["data"]
    assert [run["run_id"] for run in runs[:2]] == [second["run_id"], first["run_id"]]
    assert runs[0]["counts"]["findings_violated"] == 0

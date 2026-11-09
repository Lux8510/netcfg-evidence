"""Regressions for the W3 targeted review (2026-11-09)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import sessionmaker

from domain.netcfg import service
from domain.netcfg.drift import diff_configs
from domain.netcfg.parser import parse_frr_config
from domain.netcfg.rules import run_rules
from engine.bootstrap import ensure_append_only
from engine.db import Base, make_engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def r1_text() -> str:
    return (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8")


@pytest.fixture()
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'w3.db'}")
    Base.metadata.create_all(engine)
    ensure_append_only(engine)
    return sessionmaker(bind=engine, expire_on_commit=False), engine


# C04: description carries its own line number.
def test_description_has_its_own_line_number(r1_text):
    parsed = parse_frr_config(r1_text)
    interface = parsed.interfaces["eth2"]
    assert interface.description_line == 11
    changed = r1_text.replace(" description r1-to-r2-primary", " description core-uplink")
    report = diff_configs(parsed, parse_frr_config(changed))
    item = report.items[0]
    assert item.path == "interface.eth2.description"
    assert item.old_lines == (11,) and item.new_lines == (11,)


# C10: a command inside router ospf that we cannot parse blocks "satisfied".
def test_unparsed_command_inside_ospf_process_is_undetermined():
    result = run_rules(parse_frr_config("router ospf\n ospf opaque-lsa\n!\n"))
    assert result["rules_satisfied"] == 0
    assert result["findings_undetermined"] == 4


# C09: a tab-separated `router ospf` still carries evidence.
def test_tab_separated_ospf_still_has_evidence():
    result = run_rules(parse_frr_config("router\tospf 1\n"))
    assert result["findings_undetermined"] == 4
    assert all(finding.evidence for finding in result["findings"])


# C06: configuration versions cannot be deleted, only appended.
def test_config_versions_cannot_be_deleted(factory, r1_text):
    sessions, engine = factory
    with sessions() as db:
        device = service.create_device(db, "r1")
        version, _ = service.import_config(db, device.id, r1_text, "r1")
        db.commit()
        version_id = version.id
    with pytest.raises(DatabaseError):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM config_versions WHERE id=:id"), {"id": version_id})


# C09: findings are immutable, so evidence cannot be re-pointed to another version.
def test_findings_cannot_be_repointed(factory, r1_text):
    sessions, engine = factory
    with sessions() as db:
        device = service.create_device(db, "r1")
        service.import_config(db, device.id, r1_text.replace(" ip ospf cost 10\n", ""), "broken")
        run = service.run_rules_check(db, device.id)
        finding_id = run["findings"][0]["id"]
        db.commit()
    with pytest.raises(DatabaseError):
        with engine.begin() as connection:
            connection.execute(text("UPDATE findings SET status='satisfied' WHERE id=:id"),
                               {"id": finding_id})


# C13: the evidence snapshot of a change request cannot be edited later.
def test_change_request_snapshot_is_immutable(factory, r1_text):
    from domain.netcfg import change_requests as changes

    sessions, engine = factory
    with sessions() as db:
        device = service.create_device(db, "r1")
        service.import_config(db, device.id, r1_text.replace(" ip ospf cost 10\n", ""), "broken")
        run = service.run_rules_check(db, device.id)
        request = changes.create_change_request(
            db, device.id, title="补回 cost", rationale="R001 要求显式 cost，需要人工核对后修复。",
            finding_ids=[run["findings"][0]["id"]], proposed_commands=["interface eth2"], actor="何深")
        db.commit()
        request_id = request.id
    with pytest.raises(DatabaseError):
        with engine.begin() as connection:
            connection.execute(text("UPDATE change_requests SET findings_snapshot='[]' WHERE id=:id"),
                               {"id": request_id})
    # Status transitions still work.
    with sessions() as db:
        changes.transition(db, request_id, "submit", actor="何深", note="提交")
        db.commit()
        assert db.get(changes.ChangeRequest, request_id).status == "pending_approval"


# B02: a dropped baseline index is restored at startup.
def test_baseline_index_is_restored(factory, r1_text):
    sessions, engine = factory
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX one_active_baseline"))
    ensure_append_only(engine)
    with sessions() as db:
        device = service.create_device(db, "r1")
        version, _ = service.import_config(db, device.id, r1_text, "r1")
        service.set_baseline(db, device.id, version.id)
        db.commit()
        other = service.import_config(db, device.id, r1_text.replace("hostname r1", "hostname r1b"), "r1b")[0]
    # Inserting a second active row directly must violate the unique index.
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO baselines (id, device_id, config_version_id, active, created_at) "
                "VALUES ('b2', :device, :version, 1, datetime('now'))"),
                {"device": device.id, "version": other.id})


# C12: the database itself rejects a change request built on a non-violated finding.
def test_change_request_with_non_violated_finding_is_rejected_by_database(factory, r1_text):
    sessions, engine = factory
    with sessions() as db:
        device = service.create_device(db, "r1")
        service.import_config(db, device.id, r1_text + "\ninterface eth9\n ip ospf bfd\n!\n", "broken")
        run = service.run_rules_check(db, device.id)
        undetermined = next(f for f in run["findings"] if f["status"] == "undetermined")
        db.commit()
        device_id, finding_id = device.id, undetermined["id"]
    with pytest.raises(DatabaseError):
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO change_requests (id, device_id, title, rationale, status, finding_ids, "
                "findings_snapshot, proposed_commands, created_by, updated_at, created_at) "
                "VALUES ('cr-bad', :device, 'bad', 'bad', 'draft', :fids, :snap, '[]', 'x', "
                "datetime('now'), datetime('now'))"),
                {"device": device_id, "fids": f'["{finding_id}"]', "snap": f'["{finding_id}"]'})


# C13/B03: snapshot count must equal finding_ids at insert time.
def test_change_request_snapshot_must_match_finding_ids(factory, r1_text):
    from domain.netcfg import change_requests as changes

    sessions, engine = factory
    with sessions() as db:
        device = service.create_device(db, "r1")
        service.import_config(db, device.id, r1_text.replace(" ip ospf cost 10\n", ""), "broken")
        run = service.run_rules_check(db, device.id)
        request = changes.create_change_request(
            db, device.id, title="补回 cost", rationale="R001 要求显式 cost，需要人工核对后修复。",
            finding_ids=[run["findings"][0]["id"]], proposed_commands=["interface eth2"], actor="何深")
        db.commit()
        device_id, finding_id = device.id, run["findings"][0]["id"]
    with pytest.raises(DatabaseError):
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO change_requests (id, device_id, title, rationale, status, finding_ids, "
                "findings_snapshot, proposed_commands, created_by, updated_at, created_at) "
                "VALUES ('cr-empty', :device, 'bad', 'bad', 'draft', :fids, '[]', '[]', 'x', "
                "datetime('now'), datetime('now'))"),
                {"device": device_id, "fids": f'["{finding_id}"]'})

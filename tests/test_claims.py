"""Mechanism tests for claims made in README / DESIGN.

Each test names the claim it protects, so a claim cannot drift away from code.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

from domain.netcfg import service
from engine.db import Base, make_engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def r1_text() -> str:
    return (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8")

FORBIDDEN_DEVICE_MODULES = {
    "paramiko", "netmiko", "ncclient", "scrapli", "snmp", "pysnmp",
    "pexpect", "telnetlib", "subprocess", "socket",
}


def test_claim_business_layer_never_connects_to_devices():
    """README: 业务层不连接设备；只有 collectors/ 允许只读 SSH。"""
    offenders = []
    for folder in ("domain", "engine", "api"):
        for path in (ROOT / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                for name in names:
                    if name in FORBIDDEN_DEVICE_MODULES:
                        offenders.append(f"{path.relative_to(ROOT)}:{name}")
    assert offenders == []


def test_claim_config_versions_are_append_only(tmp_path, r1_text):
    """README: 版本不可变、只追加。Enforced by a database trigger, not by convention."""
    engine = make_engine(f"sqlite:///{tmp_path / 'versions.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        device = service.create_device(db, "r1")
        version, _ = service.import_config(db, device.id, r1_text, "r1")
        db.commit()

    with factory() as db:
        with pytest.raises(DatabaseError):
            db.execute(text("UPDATE config_versions SET raw_text='tampered'"))
            db.commit()
        db.rollback()

    with factory() as db:
        assert db.execute(text("SELECT raw_text FROM config_versions")).scalar() == r1_text


def test_claim_rule_results_bind_to_their_own_version(app_client, r1_text):
    """README: 每条证据都能指回它所属的版本。"""
    device_id = app_client.post("/api/devices", json={"name": "r1"}).json()["data"]["id"]
    broken = app_client.post(f"/api/devices/{device_id}/configs",
                             json={"text": r1_text.replace(" ip ospf cost 10\n", ""),
                                   "source_name": "broken"}).json()["data"]["id"]
    fixed_text = r1_text
    fixed = app_client.post(f"/api/devices/{device_id}/configs",
                            json={"text": fixed_text, "source_name": "fixed"}).json()["data"]["id"]
    first = app_client.post(f"/api/devices/{device_id}/rules/check",
                            json={"current_version_id": broken}).json()["data"]
    app_client.post(f"/api/devices/{device_id}/rules/check", json={})

    finding = first["findings"][0]
    assert finding["config_version_id"] == broken
    line = app_client.get(f"/api/versions/{finding['config_version_id']}/lines",
                          params={"start": finding["evidence"][0]["line"], "end": finding["evidence"][0]["line"]})
    assert line.status_code == 200
    assert line.json()["data"]["version_id"] == broken != fixed


def test_claim_explanation_never_changes_the_verdict(app_client, r1_text):
    """README: 解释不能改变判定。"""
    device_id = app_client.post("/api/devices", json={"name": "r1"}).json()["data"]["id"]
    app_client.post(f"/api/devices/{device_id}/configs",
                    json={"text": r1_text.replace(" ip ospf cost 10\n", ""), "source_name": "broken"})
    run = app_client.post(f"/api/devices/{device_id}/rules/check", json={}).json()["data"]
    app_client.post(f"/api/devices/{device_id}/explanations", json={"run_id": run["run_id"]})
    findings = app_client.get(f"/api/devices/{device_id}/findings", params={"run_id": run["run_id"]}).json()["data"]
    assert [(row["rule_id"], row["status"]) for row in findings] == [("R001", "violated")]


def test_only_collectors_import_ssh_clients():
    offenders = []
    for folder in ("domain", "engine", "api", "scripts", "evals"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "paramiko" in text or "netmiko" in text or "ncclient" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
    assert (ROOT / "collectors/ssh.py").exists()


def test_collector_has_no_arbitrary_shell():
    source = (ROOT / "collectors/ssh.py").read_text(encoding="utf-8")
    for forbidden in ("subprocess", "os.system", "shell=True", "exec("):
        assert forbidden not in source

from __future__ import annotations

from pathlib import Path

import pytest

from domain.netcfg.explain import (Citation, Explanation, MockExplainer, validate_explanation)
from domain.netcfg.rules import RULES, Evidence, Finding as RuleFinding

RULE = next(rule for rule in RULES if rule.id == "R001")
FINDING = RuleFinding(
    rule_id="R001", status="violated",
    message="接口 eth2 声明为 point-to-point，但未显式设置 ip ospf cost。",
    evidence=(Evidence("config_line", 14, "no ip ospf cost", "interface.eth2.ospf.cost"),),
)


def test_good_explanation_is_accepted():
    explanation = MockExplainer().explain(FINDING, RULE)
    result = validate_explanation(explanation, FINDING, RULE)
    assert result.accepted and result.reason == "ok"
    assert "R001" in explanation.text and "14" in explanation.text


@pytest.mark.parametrize("mode,reason", [
    ("unknown_rule", "unknown_rule"),
    ("wrong_rule", "rule_mismatch"),
    ("wrong_line", "line_not_in_evidence"),
    ("no_citation", "missing_citation"),
    ("overclaim", "overclaim"),
])
def test_bad_explanations_are_rejected(mode, reason):
    explanation = MockExplainer(mode).explain(FINDING, RULE)
    assert validate_explanation(explanation, FINDING, RULE).reason == reason


def test_text_must_mention_a_cited_line():
    explanation = Explanation(text="规则 R001 认为这里不符合规范，但没有指出行号。",
                              citations=(Citation("R001", (14,)),))
    assert validate_explanation(explanation, FINDING, RULE).reason == "line_not_mentioned"


def test_text_cannot_cite_an_unlinked_rule():
    explanation = Explanation(text="规则 R001 与 R004 都要求第 14 行修改。",
                              citations=(Citation("R001", (14,)),))
    assert validate_explanation(explanation, FINDING, RULE).reason == "text_cites_unlinked_rule"


def test_text_bounds_and_control_characters():
    assert validate_explanation(Explanation(text="短", citations=(Citation("R001", (14,)),)),
                                FINDING, RULE).reason == "text_invalid"
    assert validate_explanation(Explanation(text="规则 R001 第 14 行\x07有问题", citations=(Citation("R001", (14,)),)),
                                FINDING, RULE).reason == "text_invalid"
    assert validate_explanation(Explanation(text="规则 R001 第 14 行" + "x" * 2100,
                                            citations=(Citation("R001", (14,)),)),
                                FINDING, RULE).reason == "text_invalid"


def test_citation_without_lines_is_rejected():
    explanation = Explanation(text="规则 R001 认为第 14 行有问题。", citations=(Citation("R001", ()),))
    assert validate_explanation(explanation, FINDING, RULE).reason == "missing_citation"


def test_multiple_citations_all_must_be_evidence():
    good = Explanation(text="规则 R001 第 14 行与第 15 行都要看。",
                       citations=(Citation("R001", (14,)), Citation("R001", (15,))))
    assert validate_explanation(good, FINDING, RULE, evidence_lines=[14, 15]).accepted
    bad = Explanation(text="规则 R001 第 14 行与第 16 行都要看。",
                      citations=(Citation("R001", (14,)), Citation("R001", (16,))))
    assert validate_explanation(bad, FINDING, RULE, evidence_lines=[14, 15]).reason == "line_not_in_evidence"


@pytest.fixture()
def violated_run(app_client):
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "samples/frr/r1.conf").read_text(encoding="utf-8")
    device_id = app_client.post("/api/devices", json={"name": "explain-r1"}).json()["data"]["id"]
    broken = text.replace(" ip ospf cost 10\n", "")
    app_client.post(f"/api/devices/{device_id}/configs", json={"text": broken, "source_name": "r1"})
    return device_id, app_client.post(f"/api/devices/{device_id}/rules/check", json={}).json()["data"]["run_id"]


def test_api_explains_latest_run_and_is_idempotent(app_client, violated_run):
    device_id, run_id = violated_run
    first = app_client.post(f"/api/devices/{device_id}/explanations", json={"run_id": run_id})
    assert first.status_code == 201, first.text
    rows = first.json()["data"]
    assert len(rows) == 1 and rows[0]["status"] == "accepted" and rows[0]["reason"] == "ok"
    assert rows[0]["citations"][0]["rule_id"] == "R001"
    assert rows[0]["model"] == "mock"

    again = app_client.post(f"/api/devices/{device_id}/explanations", json={})
    assert again.status_code == 201
    assert [row["id"] for row in again.json()["data"]] == [row["id"] for row in rows]

    listed = app_client.get(f"/api/devices/{device_id}/explanations").json()["data"]
    assert len(listed) == 1
    assert app_client.get(f"/api/explanations/{rows[0]['id']}").json()["data"]["text"]
    assert app_client.get("/api/explanations/missing").status_code == 404


def test_api_reports_when_no_findings_exist(app_client):
    device_id = app_client.post("/api/devices", json={"name": "no-findings"}).json()["data"]["id"]
    response = app_client.post(f"/api/devices/{device_id}/explanations", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_FINDINGS"


def test_rejected_explanations_are_persisted(tmp_path):
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from domain.netcfg import service
    from domain.netcfg.explain import explain_run
    from engine.db import Base, make_engine
    from engine.models import Explanation, Finding

    engine = make_engine(f"sqlite:///{tmp_path / 'reject.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        device = service.create_device(db, "r1")
        text = (Path(__file__).resolve().parents[1] / "samples/frr/r1.conf").read_text(encoding="utf-8")
        service.import_config(db, device.id, text.replace(" ip ospf cost 10\n", ""), "r1")
        run = service.run_rules_check(db, device.id)
        rows = explain_run(db, device.id, run["run_id"], MockExplainer("wrong_line"))
        assert [row["status"] for row in rows] == ["rejected"]
        assert rows[0]["reason"] == "line_not_in_evidence"
        db.commit()

    with factory() as db:
        stored = list(db.scalars(select(Explanation)))
        assert len(stored) == 1 and stored[0].status == "rejected"
        assert db.scalar(select(Finding)) is not None
        # A rejected explanation does not change the finding.
        assert db.scalar(select(Finding)).status == "violated"


def test_cross_device_rule_ids_are_known():
    from domain.netcfg.cross_rules import CROSS_RULES
    from domain.netcfg.explain import Citation, Explanation, validate_explanation
    from domain.netcfg.rules import Evidence, Finding as RuleFinding, Rule

    rule_id, title = CROSS_RULES[0]
    rule = Rule(id=rule_id, title=title, rationale="链路两端必须一致", source="测试", severity="error",
                check=lambda parsed: [])
    finding = RuleFinding(rule_id=rule_id, status="violated", message="两端不一致",
                          evidence=(Evidence("config_line", 28, "area 0.0.0.0"),))
    explanation = Explanation(text=f"规则 {rule_id} 第 28 行显示两端区域不一致。",
                              citations=(Citation(rule_id, (28,)),))
    assert validate_explanation(explanation, finding, rule).accepted


def test_negated_disclaimer_is_not_an_overclaim():
    explanation = Explanation(
        text="规则 R001 第 14 行缺少 cost；本解释只说明判定，不表示已修改或下发任何配置。",
        citations=(Citation("R001", (14,)),))
    assert validate_explanation(explanation, FINDING, RULE).accepted

    real_claim = Explanation(text="规则 R001 第 14 行有问题，已修改配置并下发。",
                             citations=(Citation("R001", (14,)),))
    assert validate_explanation(real_claim, FINDING, RULE).reason == "overclaim"

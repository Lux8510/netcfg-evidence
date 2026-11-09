"""Explanation layer: the model may only explain a finding, never decide one.

An explanation is accepted only if:
- it cites the finding's own rule id,
- every cited line is one of the finding's evidence lines,
- at least one cited line number appears in the text,
- it does not claim the change was already applied.

Rejected explanations are still persisted (for audit) but marked as rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from engine.models import Finding
from sqlalchemy import select
from sqlalchemy.orm import Session

from .rules import RULES, Evidence, Finding as RuleFinding, Rule

PROMPT_VERSION = "mock-explain-1"
MAX_TEXT = 2000
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
OVERCLAIM_RE = re.compile(r"(已修改|已执行|已修复|已经变更|已下发|已完成变更)")
RULE_ID_RE = re.compile(r"\bR(\d{3})\b")
LINE_LIST_RE = re.compile(r"第\s*(\d+(?:\s*[、,，和及与]\s*\d+)*)\s*行")
CN_LINE_RE = re.compile(r"第\s*[一二三四五六七八九十百零〇两]+\s*行")

ACCEPT = "accepted"
REJECT = "rejected"


class ProviderError(Exception):
    """Raised by a model adapter; the message must never contain credentials."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class Citation:
    rule_id: str
    lines: tuple[int, ...]

    def as_dict(self) -> dict:
        return {"rule_id": self.rule_id, "lines": list(self.lines)}


@dataclass(frozen=True)
class Explanation:
    text: str
    citations: tuple[Citation, ...]
    model: str = "mock"
    prompt_version: str = PROMPT_VERSION
    usage: dict | None = None


@dataclass(frozen=True)
class Validation:
    accepted: bool
    reason: str


class Explainer(Protocol):
    name: str

    def explain(self, finding: RuleFinding, rule: Rule) -> Explanation: ...


class MockExplainer:
    """Deterministic stand-in for a real model. No network, no key.

    `mode` exists so tests can prove the validator rejects bad output.
    """

    name = "mock"

    def __init__(self, mode: str = "good") -> None:
        self.mode = mode

    def explain(self, finding: RuleFinding, rule: Rule) -> Explanation:
        lines = tuple(sorted({item.line for item in finding.evidence if item.line}))
        quoted = next((item.text for item in finding.evidence if item.text), "")
        if self.mode == "good":
            line_text = "、".join(str(line) for line in lines) or "无"
            text = (f"规则 {rule.id}（{rule.title}）要求：{rule.rationale}\n"
                    f"第 {line_text} 行的配置是「{quoted}」，因此判定为{finding.status}。\n"
                    f"本说明只解释已有判定，不修改任何配置。")
            return Explanation(text=text, citations=(Citation(rule.id, lines),), model=self.name)
        if self.mode == "unknown_rule":
            text = f"规则 R999 认为第 {lines[0] if lines else 1} 行有问题。"
            return Explanation(text=text, citations=(Citation("R999", lines),), model=self.name)
        if self.mode == "wrong_rule":
            other = "R002" if rule.id != "R002" else "R001"
            text = f"规则 {other} 适用于第 {lines[0] if lines else 1} 行。"
            return Explanation(text=text, citations=(Citation(other, lines),), model=self.name)
        if self.mode == "wrong_line":
            wrong = (max(lines) + 1) if lines else 1
            text = f"规则 {rule.id} 指出第 {wrong} 行违反了要求。"
            return Explanation(text=text, citations=(Citation(rule.id, (wrong,)),), model=self.name)
        if self.mode == "no_citation":
            return Explanation(text=f"规则 {rule.id} 认为这里配置不符合规范。", citations=(), model=self.name)
        if self.mode == "overclaim":
            line = lines[0] if lines else 1
            text = f"规则 {rule.id}：第 {line} 行有问题，已修改配置并下发。"
            return Explanation(text=text, citations=(Citation(rule.id, lines),), model=self.name)
        raise ValueError(f"unknown mock mode: {self.mode}")


def validate_explanation(explanation: Explanation, finding: RuleFinding, rule: Rule,
                         evidence_lines: Sequence[int] | None = None) -> Validation:
    text = explanation.text or ""
    if not 10 <= len(text) <= MAX_TEXT:
        return Validation(False, "text_invalid")
    if CONTROL_RE.search(text):
        return Validation(False, "text_invalid")
    if OVERCLAIM_RE.search(text):
        return Validation(False, "overclaim")

    allowed = set(evidence_lines if evidence_lines is not None
                  else [item.line for item in finding.evidence if item.line])
    if not explanation.citations:
        return Validation(False, "missing_citation")

    for citation in explanation.citations:
        if citation.rule_id not in {rule_item.id for rule_item in RULES}:
            return Validation(False, "unknown_rule")
        if citation.rule_id != rule.id:
            return Validation(False, "rule_mismatch")
        if not citation.lines:
            return Validation(False, "missing_citation")
        if any(line not in allowed for line in citation.lines):
            return Validation(False, "line_not_in_evidence")

    if CN_LINE_RE.search(text):
        # A line number we cannot normalise is still a claim; refuse instead of ignoring it.
        return Validation(False, "line_not_normalized")

    mentioned: set[int] = set()
    for group in LINE_LIST_RE.findall(text):
        mentioned.update(int(number) for number in re.findall(r"\d+", group))
    cited = {line for citation in explanation.citations for line in citation.lines}
    if not mentioned:
        return Validation(False, "line_not_mentioned")
    if mentioned != cited:
        # An extra line in prose is still a claim: it must match the structured citations.
        return Validation(False, "line_mismatch")

    for match in RULE_ID_RE.finditer(text):
        if f"R{match.group(1)}" not in {citation.rule_id for citation in explanation.citations}:
            return Validation(False, "text_cites_unlinked_rule")

    return Validation(True, "ok")


def rule_by_id(rule_id: str) -> Rule | None:
    return next((rule for rule in RULES if rule.id == rule_id), None)


def _finding_payload(row: Finding) -> RuleFinding:
    evidence = tuple(
        Evidence(kind=item.get("kind", "config_line"), line=item.get("line"),
                 text=item.get("text", ""), path=item.get("path"))
        for item in row.evidence
    )
    return RuleFinding(rule_id=row.rule_id, status=row.status, message=row.message, evidence=evidence)


def explain_run(db: Session, device_id: str, run_id: str, explainer: Explainer | None = None) -> list[dict]:
    from engine.models import Explanation as ExplanationRow

    explainer = explainer or MockExplainer()
    rows = list(db.scalars(
        select(Finding).where(Finding.device_id == device_id, Finding.run_id == run_id)
        .order_by(Finding.created_at, Finding.id)
    ))
    if not rows:
        from .service import ServiceError
        raise ServiceError("NO_FINDINGS", "该执行没有 Finding 可解释", 409)

    existing = {row.finding_id: row for row in db.scalars(
        select(ExplanationRow).where(ExplanationRow.run_id == run_id)
    )}
    results: list[dict] = []
    for row in rows:
        if row.id in existing:
            results.append(explanation_payload(existing[row.id]))
            continue
        rule = rule_by_id(row.rule_id)
        finding = _finding_payload(row)
        evidence_lines = [item.get("line") for item in row.evidence if item.get("line")]
        if rule is None:
            explanation = Explanation(text="规则不存在，无法解释。", citations=())
            validation = Validation(False, "unknown_rule")
        else:
            try:
                explanation = explainer.explain(finding, rule)
            except ProviderError as exc:
                explanation = Explanation(text=f"[模型输出被丢弃] {exc.message}", citations=(),
                                          model=getattr(explainer, "name", "unknown"))
                validation = Validation(False, exc.reason)
            else:
                validation = validate_explanation(explanation, finding, rule, evidence_lines)

        stored = ExplanationRow(
            run_id=run_id, device_id=device_id, finding_id=row.id, rule_id=row.rule_id,
            status=ACCEPT if validation.accepted else REJECT, reason=validation.reason,
            text=explanation.text, model=explanation.model, prompt_version=explanation.prompt_version,
            citations=[citation.as_dict() for citation in explanation.citations],
            usage=explanation.usage,
        )
        try:
            with db.begin_nested():
                db.add(stored)
                db.flush()
        except Exception:
            reused = db.scalar(select(ExplanationRow).where(
                ExplanationRow.finding_id == row.id,
                ExplanationRow.prompt_version == explanation.prompt_version,
                ExplanationRow.model == explanation.model))
            if reused is None:
                raise
            results.append(explanation_payload(reused))
            continue
        results.append(explanation_payload(stored))
    return results


def explanation_payload(row) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "device_id": row.device_id,
        "finding_id": row.finding_id,
        "rule_id": row.rule_id,
        "status": row.status,
        "reason": row.reason,
        "text": row.text,
        "model": row.model,
        "prompt_version": row.prompt_version,
        "citations": row.citations,
        "usage": row.usage,
        "created_at": row.created_at.isoformat(),
    }


def latest_run_id(db: Session, device_id: str) -> str | None:
    """Use the run table, so a run with zero findings still counts as the latest."""
    from engine.models import RuleRun
    return db.scalar(
        select(RuleRun.id).where(RuleRun.device_id == device_id)
        .order_by(RuleRun.created_at.desc(), RuleRun.id.desc()).limit(1)
    )


def list_explanations(db: Session, device_id: str, run_id: str | None = None) -> list:
    from engine.models import Explanation as ExplanationRow
    query = select(ExplanationRow).where(ExplanationRow.device_id == device_id)
    if run_id:
        query = query.where(ExplanationRow.run_id == run_id)
    return list(db.scalars(query.order_by(ExplanationRow.created_at.desc(), ExplanationRow.id)))


def get_explanation(db: Session, explanation_id: str):
    from engine.models import Explanation as ExplanationRow
    row = db.get(ExplanationRow, explanation_id)
    if row is None:
        from .service import ServiceError
        raise ServiceError("EXPLANATION_NOT_FOUND", "解释记录不存在", 404)
    return row

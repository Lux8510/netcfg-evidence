"""Adversarial cases for the explanation validator. Expectation is hand-declared."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "evals/explain_cases.json"

EVIDENCE = [{"kind": "config_line", "line": 14, "text": "no ip ospf cost",
             "path": "interface.eth2.ospf.cost"}]
EVIDENCE_TWO = EVIDENCE + [{"kind": "config_line", "line": 13, "text": "ip ospf network point-to-point",
                            "path": "interface.eth2.ospf.network_type"}]


def case(case_id: str, description: str, text: str, citations: list[dict],
         accepted: bool, reason: str, evidence: list[dict] | None = None) -> dict:
    return {
        "id": case_id,
        "description": description,
        "rule_id": "R001",
        "status": "violated",
        "evidence": evidence or EVIDENCE,
        "explanation": {"text": text, "citations": citations},
        "expected": {"accepted": accepted, "reason": reason},
    }


CASES = [
    case("E01", "正确解释：引用规则与证据行", 
         "规则 R001 要求点到点接口显式设置 cost。第 14 行的配置是「no ip ospf cost」，因此判定为 violated。",
         [{"rule_id": "R001", "lines": [14]}], True, "ok"),
    case("E02", "引用不存在的规则 R999",
         "规则 R999 认为第 14 行有问题。", [{"rule_id": "R999", "lines": [14]}], False, "unknown_rule"),
    case("E03", "引用其他规则 R002",
         "规则 R002 适用于第 14 行。", [{"rule_id": "R002", "lines": [14]}], False, "rule_mismatch"),
    case("E04", "引用不在证据里的行号",
         "规则 R001 指出第 15 行违反了要求。", [{"rule_id": "R001", "lines": [15]}], False, "line_not_in_evidence"),
    case("E05", "完全没有引用",
         "规则 R001 认为这里配置不符合规范。", [], False, "missing_citation"),
    case("E06", "声称已经修改配置",
         "规则 R001：第 14 行有问题，已修改配置并下发。", [{"rule_id": "R001", "lines": [14]}], False, "overclaim"),
    case("E07", "正文包含控制字符",
         "规则 R001 第 14 行\x07有问题", [{"rule_id": "R001", "lines": [14]}], False, "text_invalid"),
    case("E08", "正文超过 2000 字符",
         "规则 R001 第 14 行" + "x" * 2100, [{"rule_id": "R001", "lines": [14]}], False, "text_invalid"),
    case("E09", "正文没有提到被引用的行号",
         "规则 R001 认为这里不符合规范，但没有指出行号。", [{"rule_id": "R001", "lines": [14]}], False, "line_not_mentioned"),
    case("E10", "正文提到未引用的规则",
         "规则 R001 与 R004 都要求第 14 行修改。", [{"rule_id": "R001", "lines": [14]}], False, "text_cites_unlinked_rule"),
    case("E11", "两个证据行都被引用",
         "规则 R001 第 14 行与第 13 行共同证明该接口缺少 cost。",
         [{"rule_id": "R001", "lines": [14, 13]}], True, "ok", EVIDENCE_TWO),
    case("E12", "引用对象存在但没有任何行号",
         "规则 R001 认为第 14 行有问题。", [{"rule_id": "R001", "lines": []}], False, "missing_citation"),
]


def main() -> None:
    OUT.write_text(json.dumps({"schema_version": 1, "cases": CASES}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"wrote {len(CASES)} explanation cases -> {OUT}")


if __name__ == "__main__":
    main()

"""Evaluate rule status per case: satisfied / violated / undetermined / not_applicable."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain.netcfg.parser import parse_frr_config
from domain.netcfg.rules import RULES, run_rules

CASES = ROOT / "evals/rule_cases.json"
REPORT = ROOT / "evals/rules-report.json"


def statuses(parsed) -> dict[str, str]:
    result = run_rules(parsed)
    if result["rules_not_applicable"]:
        return {rule.id: "not_applicable" for rule in RULES}
    mapping = {rule.id: "satisfied" for rule in RULES}
    for finding in result["findings"]:
        mapping[finding.rule_id] = finding.status
    return mapping


def main() -> int:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    rows = []
    correct = 0
    total = 0
    for case in data["cases"]:
        actual = statuses(parse_frr_config(case["current"], case["id"]))
        expected = case["expected"]
        mismatches = {rule: {"expected": expected[rule], "actual": actual[rule]}
                      for rule in expected if expected[rule] != actual.get(rule)}
        passed = not mismatches
        correct += 1 if passed else 0
        total += len(expected)
        rows.append({"id": case["id"], "description": case["description"], "passed": passed,
                     "expected": expected, "actual": actual, "mismatches": mismatches})

    report = {
        "schema_version": 1,
        "suite": "rule-judgement",
        "executed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": len(rows),
        "cases_exact": correct,
        "cases_exact_rate": round(correct / len(rows), 4) if rows else 0.0,
        "rule_decisions": total,
        "rows": rows,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        mark = "PASS" if row["passed"] else "FAIL"
        print(f"[{mark}] {row['id']} {row['description']}")
        if not row["passed"]:
            print(f"       mismatches={row['mismatches']}")
    print(f"\nCases: {correct}/{len(rows)} exact; rule decisions compared: {total}")
    print(f"Report: {REPORT}")
    return 0 if correct == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

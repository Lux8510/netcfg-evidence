"""Measure how often the validator accepts/rejects an explanation for the declared reason."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain.netcfg.explain import Citation, Explanation, validate_explanation
from domain.netcfg.rules import RULES, Evidence, Finding

CASES = ROOT / "evals/explain_cases.json"
REPORT = ROOT / "evals/explain-report.json"


def main() -> int:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    rows = []
    exact = 0
    for case in data["cases"]:
        rule = next(item for item in RULES if item.id == case["rule_id"])
        evidence = tuple(Evidence(**item) for item in case["evidence"])
        finding = Finding(rule_id=case["rule_id"], status=case["status"], message="", evidence=evidence)
        explanation = Explanation(
            text=case["explanation"]["text"],
            citations=tuple(Citation(item["rule_id"], tuple(item["lines"])) for item in case["explanation"]["citations"]),
        )
        result = validate_explanation(explanation, finding, rule, [item["line"] for item in case["evidence"]])
        expected = case["expected"]
        passed = result.accepted == expected["accepted"] and result.reason == expected["reason"]
        exact += 1 if passed else 0
        rows.append({"id": case["id"], "description": case["description"], "passed": passed,
                     "expected": expected, "actual": {"accepted": result.accepted, "reason": result.reason}})

    report = {
        "schema_version": 1,
        "suite": "explanation-validation",
        "executed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": len(rows),
        "cases_exact": exact,
        "cases_exact_rate": round(exact / len(rows), 4) if rows else 0.0,
        "rows": rows,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(f"[{'PASS' if row['passed'] else 'FAIL'}] {row['id']} {row['description']}")
        if not row["passed"]:
            print(f"       expected={row['expected']} actual={row['actual']}")
    print(f"\nCases: {exact}/{len(rows)} exact")
    print(f"Report: {REPORT}")
    return 0 if exact == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

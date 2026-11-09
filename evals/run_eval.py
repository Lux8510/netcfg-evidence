"""Evaluate drift detection against the hand-labelled case set.

Reports per-case exact match and aggregate precision/recall over drift paths.
No model calls; runs offline in milliseconds.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain.netcfg.drift import diff_configs
from domain.netcfg.parser import parse_frr_config

CASES = ROOT / "evals/cases.json"
REPORT = ROOT / "evals/report.json"


def check_items(detected_items: dict[str, dict], expected_items: list[dict]) -> list[str]:
    """Compare declared fields per item so that an old/new swap cannot pass."""
    problems: list[str] = []
    for expected in expected_items:
        path = expected["path"]
        actual = detected_items.get(path)
        if actual is None:
            problems.append(f"missing item {path}")
            continue
        for field, value in expected.items():
            if field == "path":
                continue
            if field == "old_not_null":
                if (actual.get("old") is not None) != bool(value):
                    problems.append(f"{path}: old evidence mismatch")
                continue
            actual_value = actual.get(field)
            if field in {"old_lines", "new_lines"}:
                actual_value = list(actual_value or [])
            if actual_value != value:
                problems.append(f"{path}.{field}: expected {value!r}, got {actual_value!r}")
    return problems


def main() -> int:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    baseline = parse_frr_config(data["baseline"], "baseline")

    true_positive = false_positive = false_negative = 0
    exact = 0
    item_checks = 0
    item_failures = 0
    rows = []

    for case in data["cases"]:
        current = parse_frr_config(case["current"], case["id"])
        report = diff_configs(baseline, current)
        detected = {item.path for item in report.items}
        expected = set(case["expected_paths"])
        tp = len(detected & expected)
        fp = len(detected - expected)
        fn = len(expected - detected)
        true_positive += tp
        false_positive += fp
        false_negative += fn
        expected_items = case.get("expected_items", [])
        item_checks += len(expected_items)
        item_problems = check_items({item["path"]: item for item in report.as_dict()["items"]}, expected_items)
        item_failures += len(item_problems)
        missing_evidence = [item["path"] for item in report.as_dict()["items"]
                            if not item["old_lines"] and not item["new_lines"]]
        passed = detected == expected and not item_problems and not missing_evidence
        exact += int(passed)
        rows.append({"id": case["id"], "description": case["description"], "passed": passed,
                     "detected": sorted(detected), "expected": sorted(expected),
                     "item_problems": item_problems, "missing_evidence": missing_evidence})

    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    total = len(rows)
    report = {
        "schema_version": 1,
        "suite": "drift-detection",
        "executed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": total,
        "exact_match": exact,
        "exact_match_rate": round(exact / total, 4) if total else 0.0,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "item_checks": item_checks,
        "item_failures": item_failures,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "rows": rows,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for row in rows:
        mark = "PASS" if row["passed"] else "FAIL"
        print(f"[{mark}] {row['id']} {row['description']}")
        if not row["passed"]:
            print(f"       expected={row['expected']}")
            print(f"       detected={row['detected']}")
            if row["item_problems"]:
                print(f"       item problems={row['item_problems']}")
            if row["missing_evidence"]:
                print(f"       missing evidence lines={row['missing_evidence']}")
    print(f"\nCases: {exact}/{total} exact match; precision={precision:.4f}; recall={recall:.4f}")
    print(f"Declared item checks: {item_checks}, failures: {item_failures}")
    print(f"Report: {REPORT}")
    return 0 if exact == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

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


def main() -> int:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    baseline = parse_frr_config(data["baseline"], "baseline")

    true_positive = false_positive = false_negative = 0
    exact = 0
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
        passed = detected == expected
        exact += int(passed)
        rows.append({"id": case["id"], "description": case["description"], "passed": passed,
                     "detected": sorted(detected), "expected": sorted(expected)})

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
    print(f"\nCases: {exact}/{total} exact match; precision={precision:.4f}; recall={recall:.4f}")
    print(f"Report: {REPORT}")
    return 0 if exact == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

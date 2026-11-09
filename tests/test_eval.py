"""The evaluation must fail when the implementation swaps old and new values."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.run_eval import check_items, main

ROOT = Path(__file__).resolve().parents[1]


def test_eval_suite_passes():
    assert main() == 0


def test_swapped_old_new_is_rejected():
    expected = [{"path": "interface.eth2.ospf.cost", "kind": "changed", "old": 10, "new": 20,
                 "old_lines": [14], "new_lines": [14]}]
    swapped = [{"path": "interface.eth2.ospf.cost", "kind": "changed", "old": 20, "new": 10,
                "old_lines": [14], "new_lines": [14]}]
    assert check_items({"interface.eth2.ospf.cost": swapped[0]}, expected) != []
    assert check_items({"interface.eth2.ospf.cost": expected[0]}, expected) == []


def test_wrong_kind_or_evidence_line_is_rejected():
    expected = [{"path": "interface.eth3", "kind": "removed", "new": None}]
    wrong_kind = {"path": "interface.eth3", "kind": "changed", "old": {}, "new": None,
                  "old_lines": [16], "new_lines": []}
    assert check_items({"interface.eth3": wrong_kind}, expected) != []

    line_expected = [{"path": "hostname", "kind": "changed", "old_lines": [2], "new_lines": [2]}]
    wrong_line = {"path": "hostname", "kind": "changed", "old_lines": [1], "new_lines": [1]}
    assert check_items({"hostname": wrong_line}, line_expected) != []


def test_cases_declare_items_for_value_sensitive_examples():
    data = json.loads((ROOT / "evals/cases.json").read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in data["cases"]}
    for case_id in ("D02", "D13", "D16", "D20"):
        assert by_id[case_id]["expected_items"], case_id

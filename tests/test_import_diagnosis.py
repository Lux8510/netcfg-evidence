from __future__ import annotations

import hashlib
import json

import pytest

from scripts.import_diagnosis import (ImportError_, canonical, compose_rationale,
                                      import_bundle, pick_finding, verify_bundle)


def make_bundle(config: str, name: str = "import-demo") -> dict:
    bundle = {
        "schema_version": 1,
        "source": "netsecops-agent",
        "created_at_utc": "2026-11-19T00:00:00+00:00",
        "device": {"name": name},
        "diagnosis": {"status": "ospf_area_mismatch", "conclusion": "两端 Area 不一致",
                       "repair_suggestion": "统一 Area", "evidence_ids": ["E1", "E2"]},
        "config_snapshot": {"text": config,
                            "sha256": hashlib.sha256(config.encode("utf-8")).hexdigest()},
        "proposed_commands": ["no ip ospf area 0.0.0.1", "ip ospf area 0.0.0.0"],
        "citations": [{"evidence_id": "E1", "tool": "query_ospf_interface"}],
        "lab_only": True,
    }
    bundle["bundle_sha256"] = hashlib.sha256(canonical(bundle)).hexdigest()
    return bundle


def test_bundle_hash_and_snapshot_are_verified(r1_text):
    bundle = verify_bundle(make_bundle(r1_text))
    assert bundle["bundle_sha256"]
    bundle["diagnosis"]["status"] = "normal"
    with pytest.raises(ImportError_):
        verify_bundle(bundle)


def test_import_creates_change_request_linked_to_own_finding(app_client, r1_text):
    broken = r1_text.replace(" ip ospf cost 10\n", "").replace(" passive-interface lo\n", "")
    bundle = make_bundle(broken, name="bridge-demo")
    result = import_bundle(app_client, bundle)
    assert result["status"] == "imported"
    change = app_client.get(f"/api/change-requests/{result['change_request_id']}").json()["data"]
    assert change["finding_ids"] == [result["finding_id"]]
    assert result["bundle_sha256"][:8] in change["rationale"]
    assert change["proposed_commands"] == bundle["proposed_commands"]


def test_clean_config_imports_without_a_change_request(app_client, r1_text):
    result = import_bundle(app_client, make_bundle(r1_text, name="bridge-clean"))
    assert result["status"] == "imported_without_finding"
    assert "change_request_id" not in result


def test_pick_finding_prefers_requested_rule():
    check = {"findings": [
        {"id": "a", "rule_id": "R001", "status": "violated"},
        {"id": "b", "rule_id": "R003", "status": "violated"},
    ]}
    assert pick_finding(check, "R003")["id"] == "b"
    assert pick_finding(check)["id"] == "a"
    assert pick_finding({"findings": []}) is None


def test_rationale_records_source_and_keeps_bound():
    bundle = make_bundle("hostname r1\n")
    rationale = compose_rationale(bundle)
    assert "netsecops-agent" in rationale and bundle["bundle_sha256"][:16] in rationale
    assert len(rationale) <= 1900

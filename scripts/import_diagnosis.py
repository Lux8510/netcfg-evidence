#!/usr/bin/env python3
"""Import a NetSecOps diagnosis bundle as a netcfg-evidence change request.

The bundle is treated as untrusted input: its hash must match, its config
snapshot is re-evaluated by netcfg's own deterministic parser/rules, and the
change request is linked only to a finding netcfg produced itself. The external
diagnosis is recorded in the rationale and never changes a finding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import httpx

MAX_RATIONALE = 1900


class ImportError_(ValueError):
    pass


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("source") != "netsecops-agent":
        raise ImportError_("Bundle source is not netsecops-agent")
    if bundle.get("schema_version") != 1:
        raise ImportError_("Unsupported bundle schema version")
    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if hashlib.sha256(canonical(body)).hexdigest() != bundle.get("bundle_sha256"):
        raise ImportError_("Bundle hash does not match its contents")
    snapshot = bundle.get("config_snapshot") or {}
    text = snapshot.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ImportError_("Bundle has no config snapshot")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != snapshot.get("sha256"):
        raise ImportError_("Config snapshot hash does not match")
    if not isinstance(bundle.get("proposed_commands"), list):
        raise ImportError_("Bundle has no proposed_commands list")
    return bundle


def _data(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ImportError_(f"Non-JSON response from netcfg: {response.status_code}") from exc
    if response.status_code >= 400:
        error = (payload or {}).get("error") or {}
        raise ImportError_(f"netcfg rejected the request: {error.get('code')} {error.get('message')}")
    return payload.get("data")


def _find_or_create_device(client: httpx.Client, name: str) -> str:
    for device in _data(client.get("/api/devices")):
        if device["name"] == name:
            return device["id"]
    return _data(client.post("/api/devices", json={"name": name}))["id"]


def pick_finding(check: dict[str, Any], preferred_rule: str | None = None) -> dict[str, Any] | None:
    findings = [item for item in check.get("findings") or [] if item.get("status") == "violated"]
    if preferred_rule:
        for item in findings:
            if item.get("rule_id") == preferred_rule:
                return item
    return findings[0] if findings else None


def compose_rationale(bundle: dict[str, Any]) -> str:
    diagnosis = bundle.get("diagnosis") or {}
    text = (f"来源 netsecops-agent；诊断 {diagnosis.get('status')}；"
            f"bundle {str(bundle.get('bundle_sha256'))[:16]}；"
            f"引用证据 {len(bundle.get('citations') or [])} 条。"
            f"外部结论：{diagnosis.get('conclusion') or '未提供'}。"
            "本变更单由 netcfg 独立复算规则后关联，不采用外部结论作为判定。")
    return text[:MAX_RATIONALE]


def import_bundle(client: httpx.Client, bundle: dict[str, Any], *,
                  preferred_rule: str | None = None) -> dict[str, Any]:
    bundle = verify_bundle(bundle)
    snapshot = bundle["config_snapshot"]
    device_name = (bundle.get("device") or {}).get("name")
    if not isinstance(device_name, str) or not device_name.strip():
        raise ImportError_("Bundle has no device name")
    device_id = _find_or_create_device(client, device_name.strip())
    version = _data(client.post(f"/api/devices/{device_id}/configs", json={
        "text": snapshot["text"],
        "source_name": f"netsecops-{bundle['bundle_sha256'][:8]}",
    }))
    _data(client.post(f"/api/devices/{device_id}/baseline", json={"version_id": version["id"]}))
    check = _data(client.post(f"/api/devices/{device_id}/rules/check", json={}))
    finding = pick_finding(check, preferred_rule)
    if finding is None:
        return {"status": "imported_without_finding", "device_id": device_id,
                "version_id": version["id"], "bundle_sha256": bundle["bundle_sha256"]}
    commands = [item for item in bundle["proposed_commands"] if isinstance(item, str) and item.strip()]
    change = _data(client.post(f"/api/devices/{device_id}/change-requests", json={
        "title": f"外部诊断：{(bundle.get('diagnosis') or {}).get('status')} / {finding['rule_id']}",
        "rationale": compose_rationale(bundle),
        "finding_ids": [finding["id"]],
        "proposed_commands": commands,
    }))
    return {"status": "imported", "device_id": device_id, "version_id": version["id"],
            "finding_id": finding["id"], "change_request_id": change["id"],
            "bundle_sha256": bundle["bundle_sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=os.environ.get("NETCFG_PASSWORD"))
    parser.add_argument("--rule", help="Prefer this rule for the change request")
    args = parser.parse_args(argv)
    if not args.password:
        print("Missing password: use --password or NETCFG_PASSWORD", file=sys.stderr)
        return 2
    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        with httpx.Client(base_url=args.base_url, timeout=30) as client:
            login = client.post("/api/auth/login", json={"username": args.username, "password": args.password})
            _data(login)
            result = import_bundle(client, bundle, preferred_rule=args.rule)
    except (OSError, json.JSONDecodeError, ImportError_) as exc:
        print(f"Import error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "imported" else 1


if __name__ == "__main__":
    raise SystemExit(main())

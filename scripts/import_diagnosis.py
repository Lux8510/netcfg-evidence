#!/usr/bin/env python3
"""Import a NetSecOps diagnosis bundle through netcfg's authenticated endpoint.

This is a thin client: all validation, deduplication and persistence happen in
one server-side transaction at POST /api/devices/{device_id}/diagnosis-imports.
Exit code is 0 whenever the import was persisted (with or without a draft);
it is non-zero only for transport, authentication or validation failures.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

import httpx


def _data(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Non-JSON response from netcfg: {response.status_code}") from exc
    if response.status_code >= 400:
        error = (payload or {}).get("error") or {}
        raise RuntimeError(f"netcfg rejected the request: {error.get('code')} {error.get('message')}")
    return payload.get("data")


def _resolve_device(client: httpx.Client, device_id: str | None, device: str | None) -> str:
    if device_id:
        return device_id
    if device:
        for row in _data(client.get("/api/devices")):
            if row["name"] == device:
                return row["id"]
        raise RuntimeError(f"设备 {device!r} 不存在；请先在工作台登记设备或使用 --device-id")
    raise RuntimeError("需要 --device-id 或 --device 指定目标设备")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=os.environ.get("NETCFG_PASSWORD"))
    parser.add_argument("--device-id", help="目标设备 ID（推荐，明确绑定）")
    parser.add_argument("--device", help="目标设备名（仅匹配已登记设备，不自动创建）")
    parser.add_argument("--rule", help="指定本地规则；未命中时不建草稿也不回退")
    args = parser.parse_args(argv)
    if not args.password:
        print("Missing password: use --password or NETCFG_PASSWORD", file=sys.stderr)
        return 2
    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        with httpx.Client(base_url=args.base_url, timeout=30) as client:
            login = client.post("/api/auth/login", json={"username": args.username, "password": args.password})
            _data(login)
            device_id = _resolve_device(client, args.device_id, args.device)
            result = _data(client.post(f"/api/devices/{device_id}/diagnosis-imports", json={
                "bundle": bundle,
                "preferred_rule": args.rule,
            }))
    except (OSError, json.JSONDecodeError, RuntimeError, httpx.HTTPError) as exc:
        print(f"Import error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Persisted is success, regardless of whether a draft was created.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

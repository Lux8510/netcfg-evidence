#!/usr/bin/env python3
"""Verify the audit hash chain; exit 1 at the first broken link."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.audit_chain import verify_chain
from engine.db import SessionLocal


def main() -> int:
    with SessionLocal() as db:
        result = verify_chain(db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

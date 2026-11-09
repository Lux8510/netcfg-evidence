"""Create a local account. Password comes from NETCFG_USER_PASSWORD or a prompt."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from engine.auth import AuthError, create_user  # noqa: E402
from engine.db import SessionLocal, init_db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", required=True, choices=["editor", "approver", "admin"])
    args = parser.parse_args()

    password = os.environ.get("NETCFG_USER_PASSWORD") or getpass.getpass("密码（至少 8 位）: ")
    init_db()
    try:
        with SessionLocal.begin() as db:
            user = create_user(db, args.username, args.role, password)
            print(f"created user={user.username} role={user.role} id={user.id}")
    except AuthError as exc:
        print(f"error [{exc.code}] {exc.message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

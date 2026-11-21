#!/usr/bin/env python3
"""PostgreSQL acceptance for the diagnosis-import contract.

Checks, against a real PostgreSQL database:
  1. migration with existing data (old revision -> head preserves rows);
  2. same-hash historical and current versions coexist;
  3. concurrent imports of one bundle produce one logical import;
  4. a failure after the draft rolls back all business rows, and retry works.

Usage:
    python tools/contract_postgres.py \
      --url postgresql+psycopg://postgres:postgres@localhost:5432/netcfg \
      --netsecops ../netsecops-agent
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain.netcfg import diagnosis_import, mappings, service  # noqa: E402
from domain.netcfg.diagnosis_import import canonical  # noqa: E402
from engine import models  # noqa: E402
from engine.db import init_db  # noqa: E402


def reset_schema(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")


def alembic(url: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "NETCFG_DATABASE_URL": url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, check=check)


def count_rows(session, device_id: str) -> dict[str, int]:
    return {
        "configs": len(session.scalars(select(models.ConfigVersion).where(
            models.ConfigVersion.device_id == device_id)).all()),
        "rule_runs": len(session.scalars(select(models.RuleRun).where(
            models.RuleRun.device_id == device_id)).all()),
        "findings": len(session.scalars(select(models.Finding).where(
            models.Finding.device_id == device_id)).all()),
        "change_requests": len(session.scalars(select(models.ChangeRequest).where(
            models.ChangeRequest.device_id == device_id)).all()),
        "imports": len(session.scalars(select(models.DiagnosisImport).where(
            models.DiagnosisImport.device_id == device_id)).all()),
    }


def export_area_bundle(netsecops: Path, trace: Path, out: Path, *, suffix: str = "") -> dict:
    if suffix:
        document = json.loads(trace.read_text(encoding="utf-8"))
        trace_obj = document.get("trace") if isinstance(document.get("trace"), dict) else document
        trace_obj = dict(trace_obj)
        trace_obj["task"] = str(trace_obj.get("task")) + suffix
        trace = out.with_suffix(".trace.json")
        trace.write_text(json.dumps(trace_obj), encoding="utf-8")
    handoff = netsecops / "labs/ospf-baseline/handoff.py"
    subprocess.run([sys.executable, str(handoff), "--trace", str(trace), "--device", "r2",
                    "--target", "r2", "--out", str(out)], cwd=handoff.parent, check=True,
                   capture_output=True, text=True)
    return json.loads(out.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("CONTRACT_DATABASE_URL"))
    parser.add_argument("--netsecops", type=Path,
                        default=Path(os.environ.get("NETSECOPS_DIR", "../netsecops-agent")))
    parser.add_argument("--out", type=Path, default=ROOT / "contract-postgres-report.json")
    args = parser.parse_args(argv)
    if not args.url or not args.url.startswith("postgresql"):
        print("A PostgreSQL URL is required (CONTRACT_DATABASE_URL or --url)", file=sys.stderr)
        return 2
    netsecops = args.netsecops.resolve()
    report: dict[str, Any] = {"schema_version": 1,
                              "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "database": args.url.split("@")[-1], "checks": []}

    engine = create_engine(args.url, future=True, pool_pre_ping=True)
    reset_schema(engine)

    # 1) migration with existing data at the pre-historical revision
    old = "9d1f2c3a4b5c"
    alembic(args.url, "upgrade", old)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO devices (id, name, created_at) VALUES ('dev-old', 'legacy', :now)"), {"now": now})
        connection.execute(text(
            "INSERT INTO config_versions (id, device_id, source_name, raw_text, raw_sha256, "
            "canonical_sha256, parser_version, parsed, unparsed, line_count, created_at) "
            "VALUES ('ver-old', 'dev-old', 'legacy', 'hostname legacy', :raw, :canon, 'old', "
            "'{}'::json, '[]'::json, 1, :now)"),
            {"raw": "a" * 64, "canon": "b" * 64, "now": now})
    alembic(args.url, "upgrade", "head")
    alembic(args.url, "check")
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT device_id, source_name, historical FROM config_versions WHERE id = 'ver-old'")).one()
    assert row.device_id == "dev-old" and row.source_name == "legacy" and row.historical is False
    report["checks"].append({"check": "migration_with_data", "preserved": True,
                             "historical_default": row.historical})

    init_db(engine)  # idempotent; also installs append-only triggers
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    # 2) same hash can exist as historical and current
    with factory() as session:
        device = service.create_device(session, "coexist")
        historical, _ = service.import_config(session, device.id, "hostname coexist\n", "evidence",
                                              historical=True)
        current, _ = service.import_config(session, device.id, "hostname coexist\n", "manual")
        session.commit()
        assert historical.id != current.id
        assert historical.historical is True and current.historical is False
        rows = session.scalars(select(models.ConfigVersion).where(
            models.ConfigVersion.device_id == device.id)).all()
        assert len(rows) == 2
    report["checks"].append({"check": "historical_current_coexist", "versions": 2})

    # 3) concurrent imports of the same bundle -> one logical import
    with factory() as session:
        r1 = service.create_device(session, "r1")
        r2 = service.create_device(session, "r2")
        service.create_link(session, r1.id, "eth1", r2.id, "eth1")
        mappings.create_mapping(session, environment="lab", topology="ospf", node="r1",
                                device_id=r1.id, actor="pg-admin")
        mappings.create_mapping(session, environment="lab", topology="ospf", node="r2",
                                device_id=r2.id, actor="pg-admin")
        session.commit()
        r1_id, r2_id = r1.id, r2.id
    with tempfile.TemporaryDirectory(prefix="netcfg-pg-") as folder:
        bundle_path = Path(folder) / "area.json"
        export_area_bundle(netsecops, netsecops / "labs/ospf-baseline/tests/fixtures/traces/stage5-case-01.json",
                           bundle_path)
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

        def do_import() -> str:
            last: Exception | None = None
            for _ in range(3):
                try:
                    with factory() as session:
                        result = diagnosis_import.import_diagnosis(
                            session, r2_id, bundle, "pg-admin",
                            environment="lab", topology="ospf")
                        session.commit()
                        return result["id"]
                except Exception as exc:  # retry transient serialization/deadlock errors
                    last = exc
            raise AssertionError(f"concurrent import failed: {last}")

        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: do_import(), range(4)))
        assert len(set(ids)) == 1, ids
        with factory() as session:
            imports = session.scalars(select(models.DiagnosisImport).where(
                models.DiagnosisImport.device_id == r2_id)).all()
            assert len(imports) == 1
            assert len(session.scalars(select(models.RuleRun).where(
                models.RuleRun.device_id == r2_id)).all()) == 1
        report["checks"].append({"check": "concurrent_import_dedup", "imports": 1, "ids": len(set(ids))})

        # 4) failure after the draft rolls back all business rows, retry succeeds
        retry_bundle_path = Path(folder) / "area-retry.json"
        export_area_bundle(netsecops, netsecops / "labs/ospf-baseline/tests/fixtures/traces/stage5-case-01.json",
                           retry_bundle_path, suffix=" [pg rollback variant]")
        retry_bundle = json.loads(retry_bundle_path.read_text(encoding="utf-8"))
        with factory() as session:
            before = count_rows(session, r2_id)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE OR REPLACE FUNCTION contract_fail_import() RETURNS trigger AS $$ "
                "BEGIN RAISE EXCEPTION 'injected import failure'; END; $$ LANGUAGE plpgsql")
            connection.exec_driver_sql(
                "CREATE TRIGGER contract_fail_import BEFORE INSERT ON diagnosis_imports "
                "FOR EACH ROW EXECUTE FUNCTION contract_fail_import()")
        failed = False
        try:
            with factory() as session:
                diagnosis_import.import_diagnosis(session, r2_id, retry_bundle, "pg-admin",
                                                  environment="lab", topology="ospf")
                session.commit()
        except Exception:
            failed = True
        assert failed, "injected PostgreSQL failure was not raised"
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TRIGGER contract_fail_import ON diagnosis_imports")
            connection.exec_driver_sql("DROP FUNCTION contract_fail_import()")
        with factory() as session:
            after = count_rows(session, r2_id)
        assert after == before, (before, after)
        with factory() as session:
            retried = diagnosis_import.import_diagnosis(session, r2_id, retry_bundle, "pg-admin",
                                                        environment="lab", topology="ospf")
            session.commit()
        assert retried["status"] == "draft"
        report["checks"].append({"check": "postgres_rollback_after_draft", "unchanged": True,
                                 "retry_status": retried["status"]})

    engine.dispose()
    report["status"] = "PASS"
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

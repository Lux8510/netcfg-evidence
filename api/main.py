"""Minimal HTTP surface for the W1 slice: devices, config versions, baseline, drift."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable, Iterator

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from domain.netcfg import service
from engine.db import SessionLocal, init_db
from engine.models import DriftReport


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceBody(StrictBody):
    name: str = Field(min_length=1, max_length=80)


class ConfigBody(StrictBody):
    text: str = Field(min_length=1, max_length=2_000_000)
    source_name: str = Field(default="config", max_length=120)


class BaselineBody(StrictBody):
    version_id: str = Field(min_length=36, max_length=36)


class DriftBody(StrictBody):
    current_version_id: str | None = Field(default=None, min_length=36, max_length=36)


def create_app(session_factory: Callable[[], Session] | None = None, init_engine=None) -> FastAPI:
    maker = session_factory or SessionLocal

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(init_engine)
        yield

    app = FastAPI(title="netcfg-evidence", version="0.1.0", lifespan=lifespan)
    app.state.session_factory = maker

    @app.exception_handler(service.ServiceError)
    async def service_error(request: Request, exc: service.ServiceError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})

    def get_db() -> Iterator[Session]:
        db = maker()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def ok(data):
        return {"data": data}

    @app.get("/api/health")
    def health():
        return ok({"status": "ok", "service": "netcfg-evidence"})

    @app.post("/api/devices", status_code=201)
    def create_device(body: DeviceBody, db: Session = Depends(get_db)):
        device = service.create_device(db, body.name)
        return ok({"id": device.id, "name": device.name, "created_at": device.created_at.isoformat()})

    @app.get("/api/devices")
    def list_devices(db: Session = Depends(get_db)):
        return ok([{"id": d.id, "name": d.name, "created_at": d.created_at.isoformat()}
                   for d in service.list_devices(db)])

    @app.post("/api/devices/{device_id}/configs", status_code=201)
    def import_config(device_id: str, body: ConfigBody, db: Session = Depends(get_db)):
        version, created = service.import_config(db, device_id, body.text, body.source_name)
        payload = service.version_payload(version)
        payload["unchanged"] = not created
        return ok(payload)

    @app.get("/api/devices/{device_id}/configs")
    def list_configs(device_id: str, db: Session = Depends(get_db)):
        return ok([service.version_payload(v) for v in service.list_versions(db, device_id)])

    @app.get("/api/versions/{version_id}")
    def get_version(version_id: str, db: Session = Depends(get_db)):
        return ok(service.version_payload(service.get_version(db, version_id), include_text=True))

    @app.post("/api/devices/{device_id}/baseline", status_code=201)
    def set_baseline(device_id: str, body: BaselineBody, db: Session = Depends(get_db)):
        baseline = service.set_baseline(db, device_id, body.version_id)
        return ok({"id": baseline.id, "device_id": baseline.device_id,
                   "config_version_id": baseline.config_version_id, "active": baseline.active,
                   "created_at": baseline.created_at.isoformat()})

    @app.get("/api/devices/{device_id}/baseline")
    def get_baseline(device_id: str, db: Session = Depends(get_db)):
        service.get_device_or_404(db, device_id)
        baseline = service.active_baseline(db, device_id)
        if baseline is None:
            return ok(None)
        return ok({"id": baseline.id, "device_id": baseline.device_id,
                   "config_version_id": baseline.config_version_id, "active": baseline.active,
                   "created_at": baseline.created_at.isoformat()})

    @app.post("/api/devices/{device_id}/drift", status_code=201)
    def compute_drift(device_id: str, body: DriftBody | None = None, db: Session = Depends(get_db)):
        report = service.compute_drift(db, device_id, body.current_version_id if body else None)
        return ok(service.drift_payload(report))

    @app.get("/api/devices/{device_id}/drift")
    def list_drifts(device_id: str, db: Session = Depends(get_db)):
        return ok([service.drift_payload(r) for r in service.list_drifts(db, device_id)])

    @app.get("/api/rules")
    def list_rules():
        from domain.netcfg.rules import rules_payload
        return ok(rules_payload())

    @app.post("/api/devices/{device_id}/rules/check", status_code=201)
    def run_rules(device_id: str, body: DriftBody | None = None, db: Session = Depends(get_db)):
        version_id = body.current_version_id if body else None
        return ok(service.run_rules_check(db, device_id, version_id))

    @app.get("/api/devices/{device_id}/findings")
    def list_findings(device_id: str, run_id: str | None = None, db: Session = Depends(get_db)):
        return ok([service.finding_payload(row) for row in service.list_findings(db, device_id, run_id)])

    @app.get("/api/versions/{version_id}/lines")
    def version_lines(version_id: str, start: int = 1, end: int = 1, db: Session = Depends(get_db)):
        return ok(service.version_lines(db, version_id, start, end))

    @app.get("/api/drift/{report_id}")
    def get_drift(report_id: str, db: Session = Depends(get_db)):
        report = db.get(DriftReport, report_id)
        if report is None:
            raise service.ServiceError("DRIFT_NOT_FOUND", "漂移报告不存在", 404)
        return ok(service.drift_payload(report))

    return app


app = create_app()

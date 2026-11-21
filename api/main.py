"""Minimal HTTP surface for the W1 slice: devices, config versions, baseline, drift."""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Iterator

from dotenv import load_dotenv

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from domain.netcfg import service
from engine import observability
from engine.auth import SESSION_TTL_HOURS
from engine.db import SessionLocal, init_db
from engine.models import DriftReport


ROOT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT_DIR / "web"
# .env is git-ignored; it only ever supplies server-side secrets.
load_dotenv(ROOT_DIR / ".env")


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(StrictBody):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class DeviceBody(StrictBody):
    name: str = Field(min_length=1, max_length=80)


class ConfigBody(StrictBody):
    text: str = Field(min_length=1, max_length=2_000_000)
    source_name: str = Field(default="config", max_length=120)


class BaselineBody(StrictBody):
    version_id: str = Field(min_length=36, max_length=36)


class DriftBody(StrictBody):
    current_version_id: str | None = Field(default=None, min_length=36, max_length=36)


class RuleCheckBody(StrictBody):
    current_version_id: str | None = Field(default=None, min_length=36, max_length=36)
    include_cross: bool = False


class ExemptionBody(StrictBody):
    drift_report_id: str = Field(min_length=36, max_length=36)
    path: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=10, max_length=1000)
    expires_at: datetime
    actor: str = Field(default="", max_length=200)  # ignored: identity comes from the session


class RevokeBody(StrictBody):
    note: str = Field(min_length=1, max_length=1000)
    actor: str = Field(default="", max_length=200)  # ignored: identity comes from the session


class TargetBody(StrictBody):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=80)
    credential_ref: str | None = Field(default=None, max_length=120)
    key_path: str | None = Field(default=None, max_length=255)
    enabled: bool = True


class CollectBody(StrictBody):
    label: str = Field(default="manual", min_length=1, max_length=60)


class ChangeRunBody(StrictBody):
    note: str = Field(min_length=10, max_length=1000)


class CompleteChangeBody(StrictBody):
    note: str = Field(min_length=1, max_length=1000)


class LinkBody(StrictBody):
    device_a_id: str = Field(min_length=36, max_length=36)
    iface_a: str = Field(min_length=1, max_length=80)
    device_b_id: str = Field(min_length=36, max_length=36)
    iface_b: str = Field(min_length=1, max_length=80)


class ChangeRequestBody(StrictBody):
    # Bounds that are business rules live in the service so the error carries a domain code.
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=2000)
    finding_ids: list[str] = Field(default_factory=list, max_length=200)
    proposed_commands: list[str] = Field(default_factory=list, max_length=200)
    actor: str = Field(default="", max_length=200)  # ignored: identity comes from the session


class ExplainBody(StrictBody):
    run_id: str | None = Field(default=None, min_length=36, max_length=36)


class DiagnosisImportBody(StrictBody):
    bundle: dict
    preferred_rule: str | None = Field(default=None, max_length=16)


class TransitionBody(StrictBody):
    note: str = Field(min_length=1, max_length=1000)
    actor: str = Field(default="", max_length=80)  # ignored: identity comes from the session
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


def create_app(session_factory: Callable[[], Session] | None = None, init_engine=None) -> FastAPI:
    maker = session_factory or SessionLocal

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(init_engine)
        yield

    observability.configure_logging()
    app = FastAPI(title="netcfg-evidence", version="0.1.0", lifespan=lifespan)
    app.state.session_factory = maker

    from engine.auth import (COOKIE_NAME, AuthError, authenticate, check_login_allowed, end_session,
                             record_login_failure, reset_login_failures, resolve, start_session, user_payload)

    PUBLIC_API = {"/api/health", "/api/auth/login"}
    APPROVER_ACTION = re.compile(r"^/api/change-requests/[^/]+/(approve|reject|apply)$")

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api") or path in PUBLIC_API:
            return await call_next(request)
        db = maker()
        try:
            user = resolve(db, request.cookies.get(COOKIE_NAME))
        except AuthError as exc:
            return JSONResponse(status_code=exc.status,
                                content={"error": {"code": exc.code, "message": exc.message}})
        finally:
            db.close()
        request.state.user = user

        # Defence in depth on top of SameSite=Lax: browser cross-origin writes are rejected.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and path != "/api/auth/login":
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                from urllib.parse import urlsplit
                if urlsplit(origin).netloc != request.headers.get("host", ""):
                    return JSONResponse(status_code=403, content={"error": {
                        "code": "ORIGIN_MISMATCH", "message": "跨站写请求已被拒绝"}})

        if path == "/api/auth/logout":
            return await call_next(request)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if APPROVER_ACTION.match(path):
                if user.role not in {"approver", "admin"}:
                    return JSONResponse(status_code=403, content={"error": {
                        "code": "ROLE_REQUIRED", "message": "只有审批者或管理员可以批准/拒绝/标记执行"}})
            elif user.role not in {"editor", "admin"}:
                return JSONResponse(status_code=403, content={"error": {
                    "code": "ROLE_REQUIRED", "message": "当前角色只能审批，不能修改配置或登记数据"}})
        return await call_next(request)

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

    @app.middleware("http")
    async def observe(request: Request, call_next):
        import uuid
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        user = getattr(getattr(request, "state", None), "user", None)
        observability.record_request(request_id=request_id, method=request.method,
                                     path=request.url.path, status=response.status_code,
                                     duration_ms=duration_ms,
                                     user=getattr(user, "username", None))
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/api/metrics")
    def metrics():
        return observability.snapshot()

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
        client = request.client.host if request.client else "unknown"
        try:
            check_login_allowed(body.username, client)
        except AuthError as exc:
            raise service.ServiceError(exc.code, exc.message, exc.status) from exc
        try:
            user = authenticate(db, body.username, body.password)
        except AuthError as exc:
            record_login_failure(body.username, client)
            raise service.ServiceError(exc.code, exc.message, exc.status) from exc
        reset_login_failures(body.username, client)
        # Rotate: logging in always invalidates the session cookie it replaces.
        end_session(db, request.cookies.get(COOKIE_NAME))
        token = start_session(db, user)
        # Commit before the client can issue its next request; the dependency teardown
        # runs after the response is sent and would otherwise race the new session.
        db.commit()
        response = JSONResponse({"data": user_payload(user)})
        secure = os.environ.get("NETCFG_COOKIE_SECURE", "false").lower() == "true"
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax",
                            secure=secure, max_age=SESSION_TTL_HOURS * 3600, path="/")
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request, db: Session = Depends(get_db)):
        end_session(db, request.cookies.get(COOKIE_NAME))
        db.commit()
        response = JSONResponse({"data": {"logged_out": True}})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/api/auth/me")
    def me(request: Request):
        return {"data": user_payload(request.state.user)}

    @app.exception_handler(service.ServiceError)
    async def service_error(request: Request, exc: service.ServiceError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})


    @app.get("/api/health")
    def health():
        return ok({"status": "ok", "service": "netcfg-evidence"})

    @app.post("/api/devices", status_code=201)
    def create_device(body: DeviceBody, db: Session = Depends(get_db)):
        device = service.create_device(db, body.name)
        # Commit before the response so an immediate follow-up request cannot miss the row.
        db.commit()
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
    def list_configs(device_id: str, include_historical: bool = False, db: Session = Depends(get_db)):
        return ok([service.version_payload(v)
                   for v in service.list_versions(db, device_id, include_historical=include_historical)])

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
        from domain.netcfg import exemptions
        report = service.compute_drift(db, device_id, body.current_version_id if body else None)
        return ok(exemptions.decorate_report(db, report))

    @app.get("/api/devices/{device_id}/drift")
    def list_drifts(device_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import exemptions
        return ok([exemptions.decorate_report(db, r) for r in service.list_drifts(db, device_id)])

    @app.post("/api/devices/{device_id}/drift/exemptions", status_code=201)
    def create_exemption(device_id: str, body: ExemptionBody, request: Request,
                         db: Session = Depends(get_db)):
        from domain.netcfg import exemptions
        row = exemptions.create_exemption(db, device_id, body.drift_report_id, body.path,
                                          body.reason, request.state.user.username, body.expires_at)
        return ok(exemptions.payload(row))

    @app.get("/api/devices/{device_id}/drift/exemptions")
    def list_exemptions(device_id: str, include_inactive: bool = False, db: Session = Depends(get_db)):
        from domain.netcfg import exemptions
        return ok([exemptions.payload(row) for row in exemptions.list_exemptions(db, device_id, include_inactive)])

    @app.post("/api/exemptions/{exemption_id}/revoke")
    def revoke_exemption(exemption_id: str, body: RevokeBody, request: Request,
                         db: Session = Depends(get_db)):
        from domain.netcfg import exemptions
        return ok(exemptions.payload(exemptions.revoke_exemption(
            db, exemption_id, request.state.user.username, body.note)))

    @app.get("/api/rules")
    def list_rules():
        from domain.netcfg.rules import rules_payload
        return ok(rules_payload())

    @app.post("/api/devices/{device_id}/rules/check", status_code=201)
    def run_rules(device_id: str, body: RuleCheckBody | None = None, db: Session = Depends(get_db)):
        version_id = body.current_version_id if body else None
        include_cross = bool(body and body.include_cross)
        return ok(service.run_rules_check(db, device_id, version_id, include_cross))

    @app.get("/api/audit/verify")
    def audit_verify(db: Session = Depends(get_db)):
        from engine.audit_chain import verify_chain
        return ok(verify_chain(db))

    @app.get("/api/devices/{device_id}/audit")
    def list_audit(device_id: str, limit: int = 100, db: Session = Depends(get_db)):
        return ok([service.audit_payload(row) for row in service.list_audit(db, device_id, min(limit, 500))])

    @app.get("/api/devices/{device_id}/rules/runs")
    def list_rule_runs(device_id: str, db: Session = Depends(get_db)):
        return ok([service.rule_run_payload(row) for row in service.list_rule_runs(db, device_id)])

    @app.get("/api/devices/{device_id}/findings")
    def list_findings(device_id: str, run_id: str | None = None, db: Session = Depends(get_db)):
        return ok([service.finding_payload(row) for row in service.list_findings(db, device_id, run_id)])

    @app.get("/api/versions/{version_id}/lines")
    def version_lines(version_id: str, start: int = 1, end: int = 1, db: Session = Depends(get_db)):
        return ok(service.version_lines(db, version_id, start, end))

    @app.post("/api/devices/{device_id}/explanations", status_code=201)
    def create_explanations(device_id: str, body: ExplainBody | None = None, db: Session = Depends(get_db)):
        from domain.netcfg import explain
        service.get_device_or_404(db, device_id)
        run_id = body.run_id if body and body.run_id else explain.latest_run_id(db, device_id)
        if run_id is None:
            raise service.ServiceError("NO_FINDINGS", "该设备还没有规则检查记录", 409)
        from domain.netcfg.providers import build_explainer
        return ok(explain.explain_run(db, device_id, run_id, build_explainer()))

    @app.get("/api/devices/{device_id}/explanations")
    def list_explanations(device_id: str, run_id: str | None = None, db: Session = Depends(get_db)):
        from domain.netcfg import explain
        service.get_device_or_404(db, device_id)
        return ok([explain.explanation_payload(row) for row in explain.list_explanations(db, device_id, run_id)])

    @app.get("/api/explanations/{explanation_id}")
    def get_explanation(explanation_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import explain
        return ok(explain.explanation_payload(explain.get_explanation(db, explanation_id)))

    @app.post("/api/links", status_code=201)
    def create_link(body: LinkBody, db: Session = Depends(get_db)):
        link = service.create_link(db, body.device_a_id, body.iface_a, body.device_b_id, body.iface_b)
        return ok(service.link_payload(db, link))

    @app.get("/api/links")
    def list_links(device_id: str | None = None, db: Session = Depends(get_db)):
        return ok([service.link_payload(db, link) for link in service.list_links(db, device_id)])

    @app.delete("/api/links/{link_id}", status_code=204)
    def delete_link(link_id: str, db: Session = Depends(get_db)):
        service.delete_link(db, link_id)
        return Response(status_code=204)

    @app.put("/api/devices/{device_id}/target")
    def set_target(device_id: str, body: TargetBody, db: Session = Depends(get_db)):
        from domain.netcfg import collection
        target = collection.set_target(db, device_id, host=body.host, port=body.port,
                                       username=body.username, credential_ref=body.credential_ref,
                                       key_path=body.key_path, enabled=body.enabled)
        return ok(collection.target_payload(target))

    @app.get("/api/devices/{device_id}/target")
    def get_target(device_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import collection
        target = collection.get_target(db, device_id)
        return ok(collection.target_payload(target) if target else None)

    @app.post("/api/devices/{device_id}/collect", status_code=201)
    def collect_device(device_id: str, body: CollectBody, db: Session = Depends(get_db)):
        from domain.netcfg import collection
        version, event = collection.collect_device(db, device_id, body.label)
        return ok({"version": service.version_payload(version),
                   "event": collection.collection_payload(event)})

    @app.post("/api/devices/{device_id}/change-runs", status_code=201)
    def start_change_run(device_id: str, body: ChangeRunBody, request: Request,
                         db: Session = Depends(get_db)):
        from domain.netcfg import collection
        run = collection.start_change_run(db, device_id, body.note, request.state.user.username)
        return ok(collection.change_run_payload(db, run))

    @app.post("/api/change-runs/{run_id}/complete")
    def complete_change_run(run_id: str, body: CompleteChangeBody, db: Session = Depends(get_db)):
        from domain.netcfg import collection
        run = collection.complete_change_run(db, run_id, body.note)
        return ok(collection.change_run_payload(db, run))

    @app.get("/api/devices/{device_id}/change-runs")
    def list_change_runs(device_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import collection
        return ok([collection.change_run_payload(db, run)
                   for run in collection.list_change_runs(db, device_id)])

    @app.get("/api/change-runs/{run_id}")
    def get_change_run(run_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import collection
        return ok(collection.change_run_payload(db, collection.get_change_run(db, run_id)))

    @app.post("/api/devices/{device_id}/diagnosis-imports", status_code=201)
    def import_diagnosis(device_id: str, body: DiagnosisImportBody, request: Request,
                         db: Session = Depends(get_db)):
        from domain.netcfg import diagnosis_import
        try:
            record = diagnosis_import.import_diagnosis(
                db, device_id, body.bundle, request.state.user.username,
                preferred_rule=body.preferred_rule)
        except diagnosis_import.ImportError_ as exc:
            raise service.ServiceError("INVALID_BUNDLE", str(exc), 422) from exc
        return ok(record)

    @app.get("/api/devices/{device_id}/diagnosis-imports")
    def list_diagnosis_imports(device_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import diagnosis_import
        return ok([diagnosis_import.import_payload(row)
                   for row in diagnosis_import.list_imports(db, device_id)])

    @app.get("/api/diagnosis-imports/{import_id}")
    def get_diagnosis_import(import_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import diagnosis_import
        return ok(diagnosis_import.import_payload(diagnosis_import.get_import(db, import_id)))

    @app.post("/api/devices/{device_id}/change-requests", status_code=201)
    def create_change_request(device_id: str, body: ChangeRequestBody, request: Request,
                              db: Session = Depends(get_db)):
        from domain.netcfg import change_requests as changes
        change_request = changes.create_change_request(
            db, device_id, title=body.title, rationale=body.rationale,
            finding_ids=body.finding_ids, proposed_commands=body.proposed_commands,
            actor=request.state.user.username)
        return ok(changes.request_payload(change_request))

    @app.get("/api/devices/{device_id}/change-requests")
    def list_change_requests(device_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import change_requests as changes
        return ok([changes.request_payload(row) for row in changes.list_change_requests(db, device_id)])

    @app.get("/api/change-requests/{request_id}")
    def get_change_request(request_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import change_requests as changes
        return ok(changes.request_payload(changes.get_change_request(db, request_id)))

    @app.get("/api/change-requests/{request_id}/events")
    def change_request_events(request_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import change_requests as changes
        return ok([changes.event_payload(row) for row in changes.events(db, request_id)])

    @app.post("/api/change-requests/{request_id}/{action}")
    def change_request_transition(request_id: str, action: str, body: TransitionBody,
                                  request: Request, db: Session = Depends(get_db)):
        from domain.netcfg import change_requests as changes
        change_request = changes.transition(db, request_id, action, actor=request.state.user.username,
                                            note=body.note, idempotency_key=body.idempotency_key)
        return ok(changes.request_payload(change_request))

    @app.get("/api/drift/{report_id}")
    def get_drift(report_id: str, db: Session = Depends(get_db)):
        from domain.netcfg import exemptions
        report = db.get(DriftReport, report_id)
        if report is None:
            raise service.ServiceError("DRIFT_NOT_FOUND", "漂移报告不存在", 404)
        return ok(exemptions.decorate_report(db, report))

    if WEB_DIR.is_dir():
        # Mounted last so /api and /docs keep priority.
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()

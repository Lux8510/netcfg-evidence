from __future__ import annotations

import os
from pathlib import Path

# Tests must never call a real model or depend on a developer's .env.
os.environ["EXPLAIN_PROVIDER"] = "mock"
os.environ.setdefault("NETCFG_SESSION_HOURS", "12")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from api.main import create_app  # noqa: E402
from engine import auth  # noqa: E402
from engine.db import Base, make_engine  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "samples/frr"

PASSWORDS = {"何深": "editor-password-1", "主管": "approver-password-1", "编辑": "editor-password-2"}


@pytest.fixture(autouse=True)
def _reset_login_limiter():
    from engine import auth as auth_module
    auth_module.reset_all_login_failures()
    yield
    auth_module.reset_all_login_failures()


@pytest.fixture()
def api_app(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(session_factory=factory, init_engine=engine)
    with factory() as db:
        auth.create_user(db, "何深", "admin", PASSWORDS["何深"])
        auth.create_user(db, "主管", "approver", PASSWORDS["主管"])
        auth.create_user(db, "编辑", "editor", PASSWORDS["编辑"])
        db.commit()
    return app, factory, engine


def _login(app, username: str) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    response = client.post("/api/auth/login",
                           json={"username": username, "password": PASSWORDS[username]})
    assert response.status_code == 200, response.text
    return client


def _close(client: TestClient) -> None:
    client.__exit__(None, None, None)


@pytest.fixture()
def app_client(api_app):
    """Admin session: keeps most tests focused on the feature under test."""
    app, _, _ = api_app
    client = _login(app, "何深")
    yield client
    _close(client)


@pytest.fixture()
def editor_client(api_app):
    app, _, _ = api_app
    client = _login(app, "编辑")
    yield client
    _close(client)


@pytest.fixture()
def approver_client(api_app):
    app, _, _ = api_app
    client = _login(app, "主管")
    yield client
    _close(client)


@pytest.fixture()
def anon_client(api_app):
    app, _, _ = api_app
    client = TestClient(app)
    client.__enter__()
    yield client
    _close(client)


@pytest.fixture()
def r1_text() -> str:
    return (SAMPLES / "r1.conf").read_text(encoding="utf-8")


def create_device(client, name: str = "r1") -> str:
    response = client.post("/api/devices", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def import_config(client, device_id: str, text: str, source_name: str = "r1.conf"):
    return client.post(f"/api/devices/{device_id}/configs",
                       json={"text": text, "source_name": source_name})

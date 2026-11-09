from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.main import create_app
from engine.db import Base, make_engine

SAMPLES = Path(__file__).resolve().parents[1] / "samples/frr"


@pytest.fixture()
def app_client(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(session_factory=factory, init_engine=engine)
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def r1_text() -> str:
    return (SAMPLES / "r1.conf").read_text(encoding="utf-8")


def create_device(client, name="r1") -> str:
    response = client.post("/api/devices", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def import_config(client, device_id, text, source_name="r1.conf"):
    return client.post(f"/api/devices/{device_id}/configs", json={"text": text, "source_name": source_name})

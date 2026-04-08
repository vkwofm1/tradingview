"""Tests for the core API surface."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture(autouse=True)
def _setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    # Reset thread-local connection so new DB_PATH is picked up
    if hasattr(db._local, "conn"):
        del db._local.conn
    db.init_db()
    yield
    if hasattr(db._local, "conn"):
        db._local.conn.close()
        del db._local.conn


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_collect_unknown_collector(client):
    resp = client.post("/collect", json={"collector": "nope"})
    assert resp.status_code == 400


def test_collect_creates_job(client, monkeypatch):
    async def fake_collect(job_id, symbols=None):
        db.insert_market_data(job_id, "crypto", "BTC", {"price": 50000})
        return 1

    monkeypatch.setattr("app.collectors.crypto.collect", fake_collect)
    resp = client.post("/collect", json={"collector": "crypto"})
    assert resp.status_code == 200
    job = resp.json()
    assert job["status"] == "running"
    assert job["collector"] == "crypto"
    assert "id" in job


def test_list_jobs(client):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_query_data_empty(client):
    resp = client.get("/data")
    assert resp.status_code == 200
    assert resp.json() == []


def test_query_data_with_records(client):
    db.create_job("j1", "crypto")
    db.insert_market_data("j1", "crypto", "BTC", {"price": 50000})
    db.finish_job("j1", 1)
    resp = client.get("/data", params={"collector": "crypto", "symbol": "BTC"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["payload"]["price"] == 50000


def test_get_job_not_found(client):
    resp = client.get("/jobs/nonexistent")
    assert resp.status_code == 404

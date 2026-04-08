"""Tests for the core API surface, collectors, scheduler, and MCP server."""

import asyncio

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import db
from app.collectors import bithumb, naver_stocks, upbit
from app.main import app
from app.mcp_server import build_mcp
from app.scheduler import Scheduler


@pytest.fixture(autouse=True)
def _setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    # Reset thread-local connection so new DB_PATH is picked up
    if hasattr(db._local, "conn"):
        del db._local.conn
    db.init_db()
    # Make sure the background scheduler stays off in tests.
    monkeypatch.setenv("SCHED_DISABLED", "1")
    yield
    if hasattr(db._local, "conn"):
        db._local.conn.close()
        del db._local.conn


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Existing baseline tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# New collector tests (respx-mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upbit_collect_with_respx():
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.upbit.com/v1/ticker").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"market": "KRW-BTC", "trade_price": 100000000, "change_rate": 0.01},
                    {"market": "KRW-ETH", "trade_price": 5000000, "change_rate": -0.02},
                ],
            )
        )
        count = await upbit.collect("job-upbit", ["KRW-BTC", "KRW-ETH"])

    assert count == 2
    rows = db.query_market_data("upbit", "KRW-BTC", 5)
    assert len(rows) == 1
    assert rows[0]["payload"]["trade_price"] == 100000000


@pytest.mark.asyncio
async def test_bithumb_collect_with_respx():
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.bithumb.com/public/ticker/ALL_KRW").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "0000",
                    "data": {
                        "BTC": {
                            "closing_price": "100000000",
                            "opening_price": "98000000",
                            "max_price": "101000000",
                            "min_price": "97000000",
                            "units_traded_24H": "100",
                            "acc_trade_value_24H": "9999",
                            "fluctate_rate_24H": "1.5",
                            "fluctate_24H": "1500000",
                        },
                        "ETH": {
                            "closing_price": "5000000",
                            "opening_price": "4900000",
                            "max_price": "5100000",
                            "min_price": "4800000",
                            "units_traded_24H": "200",
                            "acc_trade_value_24H": "8888",
                            "fluctate_rate_24H": "2.0",
                            "fluctate_24H": "100000",
                        },
                        "DOGE_NOT_REQUESTED": {"closing_price": "0"},
                        "date": "1717000000",
                    },
                },
            )
        )
        count = await bithumb.collect("job-bithumb", ["BTC", "ETH"])

    # Only BTC + ETH (DOGE_NOT_REQUESTED is filtered out, "date" key skipped).
    assert count == 2
    rows = db.query_market_data("bithumb", "BTC", 5)
    assert len(rows) == 1
    assert rows[0]["payload"]["closing_price"] == "100000000"
    assert rows[0]["payload"]["date"] == "1717000000"


@pytest.mark.asyncio
async def test_bithumb_collect_api_error_raises():
    with respx.mock() as mock:
        mock.get("https://api.bithumb.com/public/ticker/ALL_KRW").mock(
            return_value=httpx.Response(200, json={"status": "5500", "message": "down"})
        )
        with pytest.raises(RuntimeError, match="Bithumb API error"):
            await bithumb.collect("job-fail")


@pytest.mark.asyncio
async def test_naver_collect_with_explicit_symbols():
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://m.stock.naver.com/api/stock/005930/integration").mock(
            return_value=httpx.Response(
                200,
                json={
                    "stockName": "삼성전자",
                    "closePrice": "75000",
                    "compareToPreviousClosePrice": "1000",
                    "fluctuationsRatio": "1.35",
                    "accumulatedTradingVolume": "12345678",
                    "stockExchangeType": {"code": "KOSPI", "name": "코스피"},
                    "localTradedAt": "2026-04-08T15:30:00+09:00",
                },
            )
        )
        mock.get("https://m.stock.naver.com/api/stock/000660/integration").mock(
            return_value=httpx.Response(
                200,
                json={
                    "stockName": "SK하이닉스",
                    "closePrice": "150000",
                    "compareToPreviousClosePrice": "-500",
                    "fluctuationsRatio": "-0.33",
                    "accumulatedTradingVolume": "1000000",
                    "stockExchangeType": {"code": "KOSPI"},
                    "localTradedAt": "2026-04-08T15:30:00+09:00",
                },
            )
        )

        count = await naver_stocks.collect("job-naver", ["005930", "000660"])

    assert count == 2
    rows = db.query_market_data("naver_stocks", "005930", 5)
    assert len(rows) == 1
    assert rows[0]["payload"]["name"] == "삼성전자"
    assert rows[0]["payload"]["current_price"] == "75000"


@pytest.mark.asyncio
async def test_naver_collect_uses_ranking_when_no_symbols():
    with respx.mock() as mock:
        mock.get("https://m.stock.naver.com/api/stocks/marketValue/KOSPI").mock(
            return_value=httpx.Response(
                200,
                json={
                    "stocks": [
                        {"itemCode": "005930"},
                        {"itemCode": "000660"},
                    ]
                },
            )
        )
        mock.get("https://m.stock.naver.com/api/stock/005930/integration").mock(
            return_value=httpx.Response(
                200,
                json={"stockName": "삼성전자", "closePrice": "75000"},
            )
        )
        mock.get("https://m.stock.naver.com/api/stock/000660/integration").mock(
            return_value=httpx.Response(
                200,
                json={"stockName": "SK하이닉스", "closePrice": "150000"},
            )
        )

        count = await naver_stocks.collect("job-rank")

    assert count == 2


# ---------------------------------------------------------------------------
# /collect endpoint registration for new collectors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["naver_stocks", "upbit", "bithumb"])
def test_collect_endpoint_accepts_new_collectors(client, monkeypatch, name):
    async def fake_collect(job_id, symbols=None):
        db.insert_market_data(job_id, name, "TEST", {"ok": True})
        return 1

    monkeypatch.setitem(
        __import__("app.main", fromlist=["COLLECTORS"]).COLLECTORS, name, fake_collect
    )
    resp = client.post("/collect", json={"collector": name})
    assert resp.status_code == 200
    body = resp.json()
    assert body["collector"] == name
    assert body["status"] == "running"


# ---------------------------------------------------------------------------
# Scheduler test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_runs_collector_then_stops(monkeypatch):
    # Force the scheduler on for this test even though _setup_db disabled it.
    monkeypatch.delenv("SCHED_DISABLED", raising=False)
    # Use a tiny interval so the loop sleeps briefly between iterations.
    monkeypatch.setenv("SCHED_INTERVAL_SEC", "60")

    calls = []

    async def fake_collector(job_id, symbols=None):
        calls.append(job_id)
        db.insert_market_data(job_id, "fake", "X", {"v": 1})
        return 1

    sched = Scheduler({"fake": fake_collector})
    await sched.start()
    # Give the loop a chance to run its first iteration (jitter is up to 5s,
    # but with interval=60 and min(5, 60)=5, so wait a bit longer).
    for _ in range(60):
        if calls:
            break
        await asyncio.sleep(0.1)
    await sched.stop()

    assert len(calls) >= 1
    jobs = db.list_jobs(5)
    assert any(j["collector"] == "fake" and j["status"] == "completed" for j in jobs)


# ---------------------------------------------------------------------------
# MCP server tests (in-process)
# ---------------------------------------------------------------------------


def test_mcp_mounted_initialize_handshake():
    """Verify the MCP streamable-HTTP transport responds at /mcp/ on the FastAPI app.

    Uses TestClient as a context manager so the FastAPI lifespan runs and the
    MCP session manager is initialized.
    """
    with TestClient(app) as c:
        resp = c.post(
            "/mcp/",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
        )
    assert resp.status_code == 200
    body = resp.text
    assert "tradingview-crawl" in body
    assert '"protocolVersion"' in body


@pytest.mark.asyncio
async def test_mcp_tools_listed_and_query():
    from mcp.shared.memory import create_connected_server_and_client_session

    mcp = build_mcp()
    # Seed a record so query_market_data has something to return.
    db.create_job("mcp-job", "upbit")
    db.insert_market_data("mcp-job", "upbit", "KRW-BTC", {"trade_price": 12345})
    db.finish_job("mcp-job", 1)

    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        tool_list = await client.list_tools()
        names = {t.name for t in tool_list.tools}
        for expected in {
            "collect_kr_stocks",
            "collect_upbit",
            "collect_bithumb",
            "collect_us_stocks",
            "collect_global_crypto",
            "query_market_data",
            "list_jobs",
            "get_job",
        }:
            assert expected in names

        result = await client.call_tool(
            "query_market_data",
            {"collector": "upbit", "symbol": "KRW-BTC", "limit": 5},
        )
        # FastMCP returns structured content as `structuredContent` for typed returns.
        payload = result.structuredContent or {}
        rows = payload.get("result") if isinstance(payload, dict) else None
        if rows is None:
            # Fallback: parse the first text content block as JSON.
            import json

            assert result.content, "Expected MCP tool to return content"
            text = result.content[0].text
            rows = json.loads(text)
        assert isinstance(rows, list)
        assert any(r["payload"]["trade_price"] == 12345 for r in rows)

import asyncio
from datetime import datetime, timedelta, timezone

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


def test_operations_dashboard_reports_api_checks_and_failure_alerts(client):
    for index in range(8):
        job_id = f"crypto-ok-{index}"
        db.create_job(job_id, "crypto")
        db.finish_job(job_id, 1)

    for index in range(2):
        job_id = f"naver-fail-{index}"
        db.create_job(job_id, "naver_stocks")
        db.finish_job(job_id, 0, "schema mismatch")

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.coingecko.com/api/v3/ping").mock(
            return_value=httpx.Response(200, json={"gecko_says": "(V3) To the Moon!"})
        )
        mock.get("https://query1.finance.yahoo.com/v8/finance/chart/AAPL").mock(
            return_value=httpx.Response(
                200,
                json={"chart": {"result": [{"meta": {"symbol": "AAPL"}}]}},
            )
        )
        mock.get(
            "https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:005930|SERVICE_RECENT_ITEM:005930&_callback="
        ).mock(
            return_value=httpx.Response(
                200,
                json={"result": {"areas": [{"datas": [{}]}]}},
            )
        )
        mock.get("https://api.upbit.com/v1/candles/minutes/1").mock(
            return_value=httpx.Response(
                200,
                json=[{"trade_price": 100000000, "candle_date_time_utc": "2026-04-10T10:00:00"}],
            )
        )
        mock.get("https://api.bithumb.com/public/candlestick/BTC_KRW/1m").mock(
            return_value=httpx.Response(200, json={"status": "0000", "data": []})
        )

        resp = client.get("/dashboard/operations", params={"failure_rate_threshold_pct": 10})

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["api_count"] == 5
    assert body["summary"]["degraded_apis"] == 1
    assert body["summary"]["collectors_over_failure_threshold"] == 1

    apis = {item["collector"]: item for item in body["apis"]}
    assert apis["crypto"]["status"] == "healthy"
    assert apis["naver_stocks"]["status"] == "degraded"
    assert apis["naver_stocks"]["schema_status"] == "changed"
    assert "result.areas.0.datas.0.nv" in apis["naver_stocks"]["missing_paths"]

    rates = {item["collector"]: item for item in body["job_failure_rates"]}
    assert rates["naver_stocks"]["failure_rate_pct"] == 100.0
    assert rates["naver_stocks"]["alert"] is True

    alert_codes = {item["code"] for item in body["alerts"]}
    assert "api_schema_changed" in alert_codes
    assert "job_failure_rate_high" in alert_codes


def test_api_health_endpoint_surfaces_upstream_failure(client):
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.coingecko.com/api/v3/ping").mock(
            return_value=httpx.Response(200, json={"gecko_says": "(V3) To the Moon!"})
        )
        mock.get("https://query1.finance.yahoo.com/v8/finance/chart/AAPL").mock(
            return_value=httpx.Response(
                200,
                json={"chart": {"result": [{"meta": {"symbol": "AAPL"}}]}},
            )
        )
        mock.get(
            "https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:005930|SERVICE_RECENT_ITEM:005930&_callback="
        ).mock(
            return_value=httpx.Response(
                200,
                json={"result": {"areas": [{"datas": [{"nv": "75000"}]}]}},
            )
        )
        mock.get("https://api.upbit.com/v1/candles/minutes/1").mock(
            return_value=httpx.Response(503, json={"error": "down"})
        )
        mock.get("https://api.bithumb.com/public/candlestick/BTC_KRW/1m").mock(
            return_value=httpx.Response(200, json={"status": "0000", "data": []})
        )

        resp = client.get("/health/apis")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    apis = {item["collector"]: item for item in body["apis"]}
    assert apis["upbit"]["status"] == "failing"
    assert any(item["code"] == "api_unreachable" and item["collector"] == "upbit" for item in body["alerts"])


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


def test_risk_dashboard_summarizes_collector_health_and_alerts(client):
    now = datetime.now(timezone.utc)

    db.create_job("job-crypto", "crypto")
    db.insert_market_data(
        "job-crypto",
        "crypto",
        "BTC",
        {"usd": 95000, "usd_market_cap": 1_800_000_000_000},
    )
    db.insert_market_data(
        "job-crypto",
        "crypto",
        "ETH",
        {"usd": 3200, "usd_market_cap": 400_000_000_000},
    )
    db.finish_job("job-crypto", 2)

    db.create_job("job-upbit", "upbit")
    for index, price in enumerate((100.0, 110.0, 90.0), start=1):
        db.insert_market_candle(
            "job-upbit",
            "upbit",
            "KRW-BTC",
            "1m",
            (now - timedelta(minutes=3 - index)).isoformat(),
            {"trade_price": price},
        )
    db.finish_job("job-upbit", 3)

    db.create_job("job-naver", "naver_stocks")
    db.finish_job("job-naver", 0, "upstream timeout")

    stale_time = (now - timedelta(hours=3)).isoformat()
    conn = db._conn()
    conn.execute(
        "UPDATE jobs SET created_at=?, finished_at=? WHERE id=?",
        (stale_time, stale_time, "job-naver"),
    )
    conn.commit()

    resp = client.get("/dashboard/risk", params={"stale_after_sec": 3600, "drawdown_alert_pct": 5})
    assert resp.status_code == 200
    body = resp.json()

    assert body["overview"]["collector_count"] == 3
    assert body["overview"]["healthy_collectors"] == 2
    assert body["overview"]["failing_collectors"] == 1
    assert body["overview"]["alert_count"] >= 2

    collectors = {item["collector"]: item for item in body["collectors"]}
    assert collectors["crypto"]["health"] == "healthy"
    assert collectors["naver_stocks"]["health"] == "failing"

    concentration = body["concentration"]
    assert concentration[0]["symbol"] == "BTC"
    assert concentration[0]["weight_pct"] > concentration[1]["weight_pct"]

    price_risk = {item["symbol"]: item for item in body["price_risk"]}
    assert round(price_risk["KRW-BTC"]["current_price"], 2) == 90.0
    assert price_risk["KRW-BTC"]["max_drawdown_pct_1h"] > 18.0

    alert_codes = {item["code"] for item in body["alerts"]}
    assert "collector_failed" in alert_codes
    assert "drawdown_breach" in alert_codes
    assert len(body["integration_gaps"]) >= 1


# ---------------------------------------------------------------------------
# New collector tests (respx-mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upbit_collect_with_respx():
    with respx.mock(assert_all_called=True) as mock:
        mock.get(
            "https://api.upbit.com/v1/candles/minutes/1",
            params={"market": "KRW-BTC", "count": 60},
        ).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"market": "KRW-BTC", "trade_price": 100000000, "candle_date_time_utc": "2026-04-10T10:00:00"},
                    {"market": "KRW-BTC", "trade_price": 100100000, "candle_date_time_utc": "2026-04-10T10:01:00"},
                ],
            )
        )
        mock.get(
            "https://api.upbit.com/v1/candles/minutes/1",
            params={"market": "KRW-ETH", "count": 60},
        ).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"market": "KRW-ETH", "trade_price": 5000000, "candle_date_time_utc": "2026-04-10T10:00:00"},
                    {"market": "KRW-ETH", "trade_price": 5100000, "candle_date_time_utc": "2026-04-10T10:01:00"},
                ],
            )
        )
        count = await upbit.collect("job-upbit", ["KRW-BTC", "KRW-ETH"])

    assert count == 4
    rows = db.query_market_candles("upbit", "KRW-BTC", "1m", 5)
    assert len(rows) == 2
    assert rows[0]["payload"]["trade_price"] == 100100000


@pytest.mark.asyncio
async def test_bithumb_collect_with_respx():
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.bithumb.com/public/candlestick/BTC_KRW/1m").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "0000",
                    "data": [
                        [1717000000000, "98000000", "100000000", "101000000", "97000000", "100"],
                        [1717000060000, "100000000", "100500000", "101500000", "99500000", "110"],
                    ],
                },
            )
        )
        mock.get("https://api.bithumb.com/public/candlestick/ETH_KRW/1m").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "0000",
                    "data": [
                        [1717000000000, "4900000", "5000000", "5100000", "4800000", "200"],
                        [1717000060000, "5000000", "5050000", "5150000", "4950000", "210"],
                    ],
                },
            )
        )
        count = await bithumb.collect("job-bithumb", ["BTC", "ETH"])

    assert count == 4
    rows = db.query_market_candles("bithumb", "BTC", "1m", 5)
    assert len(rows) == 2
    assert rows[0]["payload"]["close"] == "100500000"
    assert rows[0]["payload"]["symbol"] == "BTC"


@pytest.mark.asyncio
async def test_bithumb_collect_api_error_raises():
    with respx.mock() as mock:
        mock.get("https://api.bithumb.com/public/candlestick/BTC_KRW/1m").mock(
            return_value=httpx.Response(200, json={"status": "5500", "message": "down"})
        )
        with pytest.raises(RuntimeError, match="Bithumb API error"):
            await bithumb.collect("job-fail")


@pytest.mark.asyncio
async def test_naver_collect_with_explicit_symbols():
    with respx.mock(assert_all_called=True) as mock:
        mock.get(
            "https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:005930|SERVICE_RECENT_ITEM:005930&_callback="
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "areas": [
                            {
                                "datas": [
                                    {
                                        "nm": "삼성전자",
                                        "nv": "75000",
                                        "cv": "1000",
                                        "cr": "1.35",
                                        "aq": "12345678",
                                        "ms": "2026-04-08T15:30:00+09:00",
                                    }
                                ]
                            }
                        ]
                    },
                },
            )
        )
        mock.get(
            "https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:000660|SERVICE_RECENT_ITEM:000660&_callback="
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "areas": [
                            {
                                "datas": [
                                    {
                                        "nm": "SK하이닉스",
                                        "nv": "150000",
                                        "cv": "-500",
                                        "cr": "-0.33",
                                        "aq": "1000000",
                                        "ms": "2026-04-08T15:30:00+09:00",
                                    }
                                ]
                            }
                        ]
                    },
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
        mock.get(
            "https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:005930|SERVICE_RECENT_ITEM:005930&_callback="
        ).mock(
            return_value=httpx.Response(
                200,
                json={"result": {"areas": [{"datas": [{"nm": "삼성전자", "nv": "75000"}]}]}},
            )
        )
        mock.get(
            "https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:000660|SERVICE_RECENT_ITEM:000660&_callback="
        ).mock(
            return_value=httpx.Response(
                200,
                json={"result": {"areas": [{"datas": [{"nm": "SK하이닉스", "nv": "150000"}]}]}},
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

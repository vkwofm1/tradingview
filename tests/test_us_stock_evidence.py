from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pandas as pd
import pytest
import respx

from app import db
from app.collectors import stocks
from app.runner import run_collector
from app.scheduler import _interval_for
from app.us_stock_market import (
    completed_candles, evidence_quality, recover_completed_candles, session_bounds,
)
from app.us_stock_research import cost_model, financial_metrics, price_plan

NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def chart(interval, symbol="AAPL", *, dates=None):
    dates = dates or pd.date_range("2026-08-01", "2026-09-10")
    timestamps = []
    for day in dates:
        bounds = session_bounds(pd.Timestamp(day).date())
        if not bounds:
            continue
        opening, close = bounds
        timestamps.extend(
            [opening.timestamp()]
            if interval == "1d"
            else [
                (opening + timedelta(hours=i)).timestamp()
                for i in range(7)
                if opening + timedelta(hours=i) < close
            ]
        )
    return {
        "meta": {
            "symbol": symbol,
            "currency": "USD",
            "regularMarketPrice": 110,
            "regularMarketTime": datetime(
                2026, 9, 10, 20, tzinfo=timezone.utc
            ).timestamp(),
        },
        "timestamp": timestamps,
        "indicators": {
            "quote": [
                {
                    k: [v] * len(timestamps)
                    for k, v in {
                        "open": 109,
                        "high": 120,
                        "low": 100,
                        "close": 110,
                        "volume": 1000,
                    }.items()
                }
            ]
        },
    }


def financials():
    dates = pd.to_datetime(["2025-12-31", "2024-12-31"])
    income = pd.DataFrame(
        {
            d: {"OperatingIncome": 100, "PretaxIncome": 100, "TaxProvision": 20}
            for d in dates
        }
    )
    balance = pd.DataFrame(
        {
            dates[0]: {
                "TotalDebt": 100,
                "CashCashEquivalentsAndShortTermInvestments": 20,
                "StockholdersEquity": 200,
            },
            dates[1]: {
                "TotalDebt": 80,
                "CashCashEquivalentsAndShortTermInvestments": 20,
                "StockholdersEquity": 180,
            },
        }
    )
    cash = pd.DataFrame(
        {d: {"OperatingCashFlow": 200, "CapitalExpenditure": -50} for d in dates}
    )
    info = {
        "symbol": "AAPL",
        "currency": "USD",
        "financialCurrency": "USD",
        "sharesOutstanding": 100,
    }
    return info, income, balance, cash


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "stocks.db")
    monkeypatch.setattr(db, "DB_TYPE", "sqlite")
    db.init_db()
    monkeypatch.setattr(stocks, "_request_spacing", AsyncMock())
    monkeypatch.setattr(
        stocks,
        "_fetch_financials",
        lambda symbol, now: financial_metrics(*financials(), now=now),
    )
    monkeypatch.setattr(
        stocks, "evidence_quality", lambda payload: evidence_quality(payload, NOW)
    )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(stocks, "datetime", FixedDatetime)
    return db


def routes(router, symbol="AAPL"):
    for interval in ("1d", "60m"):
        router.get(f"{stocks.YF_URL}/{symbol}", params={"interval": interval}).respond(
            200,
            json={"chart": {"result": [chart(interval, symbol)]}},
        )


def test_complete_bars_exclude_open_and_short_session_bars():
    now = datetime(2026, 9, 10, 15, tzinfo=timezone.utc)
    assert len(completed_candles(chart("60m", dates=["2026-09-10"]), "60m", now)) == 1
    assert completed_candles(chart("1d", dates=["2026-09-10"]), "1d", now) == []
    assert len(completed_candles(chart("60m", dates=["2026-09-10"]), "60m", NOW)) == 6
    early = datetime(2026, 11, 28, tzinfo=timezone.utc)
    assert len(completed_candles(chart("60m", dates=["2026-11-27"]), "60m", early)) == 3
    assert completed_candles(chart("60m", dates=["2026-09-07"]), "60m", NOW) == []
    assert session_bounds(datetime(2026, 9, 10).date())[0].hour == 13
    assert session_bounds(datetime(2026, 11, 27).date())[0].hour == 14


def test_missing_volume_and_invalid_ohlc_never_become_complete_bars():
    data = chart("1d", dates=["2026-09-10"])
    data["indicators"]["quote"][0]["volume"][0] = None
    assert completed_candles(data, "1d", NOW) == []
    data["indicators"]["quote"][0]["volume"][0] = 100
    data["indicators"]["quote"][0]["high"][0] = 90
    assert completed_candles(data, "1d", NOW) == []


def test_financial_periods_and_explicit_formulas():
    args = financials()
    metrics = financial_metrics(*args, now=NOW)
    assert metrics["fcf"] == 150
    assert metrics["net_debt"] == 80
    assert metrics["roic_pct"] == pytest.approx(80 / 260 * 100)
    assert metrics["period_end"] == "2025-12-31"
    args[0]["sector"] = "Financial Services"
    assert financial_metrics(*args, now=NOW)["roic_pct"] is None
    args[0].pop("sector")
    args[0]["financialCurrency"] = "EUR"
    assert financial_metrics(*args, now=NOW)["roic_pct"] is None
    args[0]["financialCurrency"] = "USD"
    args[2].columns = pd.to_datetime(["2025-09-30", "2024-09-30"])
    assert financial_metrics(*args, now=NOW)["fcf"] is None


@pytest.mark.parametrize("tax_rate", [0.196144, None, float("nan"), -0.1, 1.1])
def test_missing_tax_provision_uses_only_valid_same_period_provider_rate(tax_rate):
    args = financials()
    income = args[1]
    income.drop(index="TaxProvision", inplace=True)
    income.loc["TaxRateForCalcs"] = [tax_rate, 0.5]
    metrics = financial_metrics(*args, now=NOW)
    assert metrics["inputs"]["tax_provision"] is None
    if tax_rate == 0.196144:
        assert metrics["roic_pct"] == pytest.approx(100 * (1 - tax_rate) / 260 * 100)
        assert metrics["inputs"]["tax_rate_source"] == "income_statement.TaxRateForCalcs"
    else:
        assert metrics["roic_pct"] is None


def test_reported_tax_takes_precedence_and_invalid_ratio_is_not_masked():
    args = financials()
    income = args[1]
    income.loc["TaxRateForCalcs"] = [0.5, 0.5]
    metrics = financial_metrics(*args, now=NOW)
    assert metrics["roic_pct"] == pytest.approx(80 / 260 * 100)
    assert metrics["inputs"]["effective_tax_rate"] == 0.2
    income.loc["TaxProvision"] = [-20, 20]
    assert financial_metrics(*args, now=NOW)["roic_pct"] is None


def test_conditional_entry_solves_net_3r_without_inflating_target(monkeypatch):
    monkeypatch.delenv("US_STOCK_COST_CONFIRMED", raising=False)
    daily = completed_candles(chart("1d"), "1d", NOW)
    plan = price_plan(daily, 110)
    assert plan["target"] == 120
    assert plan["stop"] < plan["entry"] < 110
    assert plan["net_reward_risk"] >= 3
    assert plan["status"] == "waiting_for_pullback"
    assert plan["cost_verified"] is False
    assert plan["execution_authorized"] is False
    assert price_plan(daily, 90)["entry"] is None
    monkeypatch.setenv("US_STOCK_ENTRY_COST_BPS", "NaN")
    assert price_plan(daily, 110)["entry"] is None


@pytest.mark.asyncio
async def test_collector_publishes_all_evidence_and_uses_financial_cache(
    isolated_db, monkeypatch
):
    with respx.mock as router:
        routes(router)
        result = await run_collector("stocks", stocks.collect, ["AAPL"])
        assert result["status"] == "completed"
        assert len(db.query_market_candles("stocks", "AAPL", "1d", 100)) >= 20
        assert len(db.query_market_candles("stocks", "AAPL", "60m", 200)) >= 120
        record = stocks.query_evidence(["AAPL"])[0]
        assert record["data_quality"]["ready"]
        assert not record["cost_verified_3r"]
        assert record["payload"]["fundamentals"]["fcf"] == 150
        assert record["payload"]["previousClose"] == 110
        monkeypatch.setattr(
            stocks,
            "_fetch_financials",
            lambda *_: pytest.fail("cached financials fetched again"),
        )
        assert (await run_collector("stocks", stocks.collect, ["AAPL"]))[
            "status"
        ] == "completed"
    stale = evidence_quality(
        record["payload"], datetime(2026, 9, 14, 21, tzinfo=timezone.utc)
    )
    assert not stale["ready"]
    assert "completed_1d" in stale["missing"]


@pytest.mark.asyncio
@pytest.mark.parametrize("interval,field", [("1d", "close"), ("1d", "volume"), ("60m", "close")])
async def test_provider_partial_bar_does_not_regress_stored_complete_evidence(
    isolated_db, interval, field
):
    with respx.mock as router:
        routes(router)
        initial = await run_collector("stocks", stocks.collect, ["AAPL"])
        assert initial["status"] == "completed"
        original = db.query_market_candles("stocks", "AAPL", interval, 1)[0]
        data = chart(interval)
        index = data["timestamp"].index(
            datetime.fromisoformat(original["payload"]["start_at"]).timestamp()
        )
        data["indicators"]["quote"][0][field][index] = None
        router.get(f"{stocks.YF_URL}/AAPL", params={"interval": interval}).respond(
            200, json={"chart": {"result": [data]}}
        )
        refreshed = await run_collector("stocks", stocks.collect, ["AAPL"])
    assert refreshed["status"] == "completed"
    evidence = stocks.query_evidence(["AAPL"])[0]
    assert evidence["data_quality"]["ready"]
    recovery = evidence["payload"]["candle_recovery"][interval]
    assert original["payload"]["start_at"] in recovery["reused_start_at"]
    saved = db.query_market_candles("stocks", "AAPL", interval, 1)[0]
    assert saved["collected_at"] == original["collected_at"]
    assert saved["payload"] == original["payload"]


@pytest.mark.asyncio
async def test_partial_latest_bar_without_valid_cache_is_failed(isolated_db):
    with respx.mock as router:
        routes(router)
        data = chart("1d")
        data["indicators"]["quote"][0]["close"][-1] = None
        router.get(f"{stocks.YF_URL}/AAPL", params={"interval": "1d"}).respond(
            200, json={"chart": {"result": [data]}}
        )
        result = await run_collector("stocks", stocks.collect, ["AAPL"])
    assert result["status"] == "failed"
    assert "completed_1d" in result["error"]
    assert db.query_market_data("stocks", "AAPL", 1) == []


def test_cache_recovery_rejects_conflicts_invalid_ohlc_and_open_bars():
    data = chart("1d", dates=["2026-09-10"])
    cached = completed_candles(data, "1d", NOW)
    data["indicators"]["quote"][0]["close"][-1] = None
    data["indicators"]["quote"][0]["volume"][-1] = 2000
    assert recover_completed_candles([], cached, data, "1d", NOW) == ([], [])
    data["indicators"]["quote"][0]["volume"][-1] = 1000
    before_close = datetime(2026, 9, 10, 19, tzinfo=timezone.utc)
    assert recover_completed_candles([], cached, data, "1d", before_close) == ([], [])
    cached[0]["low"] = 130
    assert recover_completed_candles([], cached, data, "1d", NOW) == ([], [])


def test_recovered_old_session_does_not_satisfy_next_required_close():
    data = chart("1d", dates=["2026-09-10"])
    cached = completed_candles(data, "1d", NOW)
    data["indicators"]["quote"][0]["close"][-1] = None
    later = datetime(2026, 9, 11, 21, tzinfo=timezone.utc)
    rows, _ = recover_completed_candles([], cached, data, "1d", later)
    quality = evidence_quality({"candles": {"1d": {
        "count": 20, "latest_end_at": rows[-1]["end_at"], "latest_volume": 1000,
    }}}, later)
    assert "completed_1d" in quality["missing"]


@pytest.mark.asyncio
async def test_missing_financial_evidence_cannot_mark_job_complete(isolated_db, monkeypatch):
    monkeypatch.setattr(stocks, "_financials", AsyncMock(return_value={}))
    with respx.mock as router:
        routes(router)
        result = await run_collector("stocks", stocks.collect, ["AAPL"])
    assert result["status"] == "failed"
    assert "fcf" in result["error"]


@pytest.mark.asyncio
async def test_partial_failure_keeps_successful_symbol_but_fails_parent(isolated_db):
    with respx.mock as router:
        routes(router)
        router.get(f"{stocks.YF_URL}/BAD").respond(404)
        result = await run_collector("stocks", stocks.collect, ["AAPL", "BAD"])
    assert result["status"] == "failed"
    record = db.query_market_data("stocks", "AAPL", 1)[0]
    assert db.get_job(record["job_id"])["status"] == "completed"
    assert db.query_market_data("stocks", "BAD", 1) == []


@pytest.mark.asyncio
async def test_excluded_empty_universe_does_not_restore_defaults(isolated_db):
    with respx.mock:
        db.create_job("empty", "stocks")
        assert await stocks.collect("empty", []) == 0
    assert db.query_market_data("stocks") == []


def test_atomic_snapshot_failure_rolls_back_candles(isolated_db):
    db.create_job("broken", "stocks")
    with pytest.raises(ValueError):
        db.publish_stock_snapshots(
            "broken",
            [
                {
                    "symbol": "AAPL",
                    "frames": {"1d": completed_candles(chart("1d"), "1d", NOW)},
                    "payload": {"bad": float("nan")},
                }
            ],
        )
    assert db.query_market_candles("stocks", "AAPL", "1d") == []
    assert db.get_job("broken")["status"] == "running"


def test_stocks_scheduler_refreshes_every_15_minutes(monkeypatch):
    monkeypatch.delenv("SCHED_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("SCHED_STOCKS_INTERVAL", raising=False)
    assert _interval_for("stocks") == 900


def test_broker_cost_verification_requires_both_explicit_costs(monkeypatch):
    monkeypatch.setenv("US_STOCK_COST_CONFIRMED", "1")
    monkeypatch.setenv("US_STOCK_COST_SOURCE", "test-broker-tariff")
    monkeypatch.delenv("US_STOCK_ENTRY_COST_BPS", raising=False)
    monkeypatch.delenv("US_STOCK_EXIT_COST_BPS", raising=False)
    assert not cost_model()["confirmed"]
    monkeypatch.setenv("US_STOCK_ENTRY_COST_BPS", "25")
    monkeypatch.setenv("US_STOCK_EXIT_COST_BPS", "30")
    assert cost_model()["confirmed"]


def test_evidence_api_returns_missing_fields_without_collecting(isolated_db):
    from fastapi.testclient import TestClient
    from app.main import app

    with respx.mock:
        response = TestClient(app).get("/stocks/evidence?symbols=UNKNOWN")
    assert response.status_code == 200
    record = response.json()[0]
    assert not record["data_quality"]["ready"]
    assert "fresh_price" in record["data_quality"]["missing"]
    assert not record["execution_authorized"]


@pytest.mark.asyncio
async def test_cli_batches_all_explicit_candidates(monkeypatch):
    from scripts import collect_jobs
    from types import SimpleNamespace

    runner = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(collect_jobs.db, "init_db", lambda: None)
    monkeypatch.setattr(
        collect_jobs.db, "resolve_collection_symbols", lambda name, symbols: symbols
    )
    monkeypatch.setattr(collect_jobs, "run_collector", runner)
    assert (
        await collect_jobs.cmd_us_stocks_1m(
            SimpleNamespace(symbols="AAPL,MSFT", batch_size=1)
        )
        == 0
    )
    assert [call.args[2] for call in runner.call_args_list] == [["AAPL"], ["MSFT"]]


@pytest.mark.asyncio
async def test_empty_provider_result_is_failure_not_success(isolated_db):
    with respx.mock as router:
        router.get(f"{stocks.YF_URL}/AAPL").respond(200, json={"chart": {"result": []}})
        result = await run_collector("stocks", stocks.collect, ["AAPL"])
    assert result["status"] == "failed"
    assert db.query_market_data("stocks") == []


@pytest.mark.asyncio
async def test_rate_limit_retries_are_bounded(monkeypatch):
    monkeypatch.setattr(stocks.asyncio, "sleep", AsyncMock())
    with respx.mock as router:
        route = router.get(f"{stocks.YF_URL}/AAPL").respond(429)
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.HTTPStatusError):
                await stocks._chart(client, "AAPL", "1d", "6mo")
        assert route.call_count == 3

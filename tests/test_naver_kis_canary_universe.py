"""Contract tests for the dedicated Naver-to-KIS canary universe."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import db, main as app_main
from app.collectors import naver_kis_canary_universe as collector
from app.runner import run_collector


@pytest.fixture(autouse=True)
def _setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    if hasattr(db._local, "sqlite_conn"):
        db._local.sqlite_conn.close()
        del db._local.sqlite_conn
    monkeypatch.setenv("SCHED_DISABLED", "1")
    monkeypatch.setenv("STOCK_REQUEST_DELAY_SEC", "0")
    monkeypatch.setenv("STOCK_REQUEST_JITTER_SEC", "0")
    monkeypatch.delenv("NAVER_KIS_CANARY_UNIVERSE_TOP_N", raising=False)
    db.init_db()
    yield
    if hasattr(db._local, "sqlite_conn"):
        db._local.sqlite_conn.close()
        del db._local.sqlite_conn


def _stock(
    code: str,
    *,
    price: object = "9,900",
    volume: object = "1,000",
    stock_end_type: str = "stock",
    market_status: object = "CLOSE",
    as_of: object = "2026-07-14T15:30:00+09:00",
    name: str | None = None,
    use_current_price: bool = False,
    reported_market: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "itemCode": code,
        "stockName": name or code,
        "stockEndType": stock_end_type,
        "accumulatedTradingVolumeRaw": volume,
        "localTradedAt": as_of,
        "marketStatus": market_status,
    }
    if reported_market is not None:
        market = reported_market.upper()
        row["sosok"] = "0" if market == "KOSPI" else "1"
        row["stockExchangeType"] = {
            "code": "KS" if market == "KOSPI" else "KQ",
            "nameEng": market,
            "name": market,
        }
    row["currentPrice" if use_current_price else "closePriceRaw"] = price
    return row


def _mock_market_page(
    mock: respx.MockRouter,
    market: str,
    page: int,
    total_count: int,
    stocks: list[object],
) -> None:
    mock.get(
        collector.MARKET_VALUE_URL.format(market=market),
        params={"page": page, "pageSize": collector.PAGE_SIZE},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"totalCount": total_count, "stocks": stocks},
        )
    )


@pytest.mark.asyncio
async def test_collects_both_markets_with_bounded_pagination_filters_and_ranks(
    monkeypatch,
):
    monkeypatch.setenv("NAVER_KIS_CANARY_UNIVERSE_TOP_N", "3")
    kospi_first_page = [
        _stock("005930", price="9,900", volume="100", name="eligible-low-price")
    ]
    kospi_first_page.extend(
        _stock(f"{300000 + index:06d}", stock_end_type="etf") for index in range(99)
    )
    kosdaq_rows = [
        _stock("035720", price="8,000", volume="300", name="rank-one"),
        _stock(
            "035420",
            price="7,000",
            volume="200",
            name="tie-code-order",
            use_current_price=True,
        ),
        _stock("12345", price="5,000", volume="900"),
        _stock("123456", price="10,001", volume="800"),
    ]

    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(mock, "KOSPI", 1, 101, kospi_first_page)
        _mock_market_page(
            mock,
            "KOSPI",
            2,
            101,
            [_stock("000660", price="9,000", volume="200", name="page-two")],
        )
        _mock_market_page(mock, "KOSDAQ", 1, 4, kosdaq_rows)

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

        assert len(mock.calls) == 3
        assert all("/api/stock/" not in str(call.request.url) for call in mock.calls)

    assert job["status"] == "completed"
    assert job["result_count"] == 3
    rows = db.query_market_data(collector.COLLECTOR_NAME, limit=20)
    by_code = {row["symbol"]: row["payload"] for row in rows}
    assert set(by_code) == {"035720", "000660", "035420"}
    assert by_code["035720"]["volume_rank"] == 1
    assert by_code["000660"]["volume_rank"] == 2
    assert by_code["035420"]["volume_rank"] == 3
    assert by_code["035420"]["current_price"] == 7000.0
    assert by_code["000660"]["market"] == "KOSPI"
    assert by_code["000660"]["as_of"] == "2026-07-14T15:30:00+09:00"
    assert by_code["000660"]["market_status"] == "CLOSE"
    assert by_code["000660"]["source"] == "naver_market_value"
    assert by_code["000660"]["provenance"] == {
        "endpoint": collector.MARKET_VALUE_URL.format(market="KOSPI"),
        "requested_market": "KOSPI",
        "reported_market": None,
        "page": 2,
        "page_size": 100,
        "total_count": 101,
        "price_field": "closePriceRaw",
        "volume_field": "accumulatedTradingVolumeRaw",
    }


@pytest.mark.asyncio
async def test_duplicate_code_is_collapsed_to_newest_reported_market_row(monkeypatch):
    monkeypatch.setenv("NAVER_KIS_CANARY_UNIVERSE_TOP_N", "10")
    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(
            mock,
            "KOSPI",
            1,
            1,
            [
                _stock(
                    "900290",
                    name="GRT",
                    price="2,405",
                    volume="470,140",
                    as_of="2026-07-14T15:29:00+09:00",
                    reported_market="KOSDAQ",
                )
            ],
        )
        _mock_market_page(
            mock,
            "KOSDAQ",
            1,
            1,
            [
                _stock(
                    "900290",
                    name="GRT",
                    price="2,420",
                    volume="403,982",
                    as_of="2026-07-15T15:30:00+09:00",
                    reported_market="KOSDAQ",
                )
            ],
        )

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

    assert job["status"] == "completed"
    assert job["result_count"] == 1
    rows = db.query_market_data(collector.COLLECTOR_NAME, limit=10)
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["market"] == "KOSDAQ"
    assert payload["current_price"] == 2420.0
    assert payload["provenance"]["requested_market"] == "KOSDAQ"
    assert payload["provenance"]["reported_market"] == "KOSDAQ"
    assert payload["provenance"]["duplicates_collapsed"] == 1
    assert payload["provenance"]["duplicate_sources"] == [
        {"requested_market": "KOSPI", "page": 1},
        {"requested_market": "KOSDAQ", "page": 1},
    ]


@pytest.mark.asyncio
async def test_duplicate_code_with_conflicting_names_fails_closed():
    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(
            mock,
            "KOSPI",
            1,
            1,
            [_stock("900290", name="GRT-A", reported_market="KOSDAQ")],
        )
        _mock_market_page(
            mock,
            "KOSDAQ",
            1,
            1,
            [_stock("900290", name="GRT-B", reported_market="KOSDAQ")],
        )

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

    assert job["status"] == "failed"
    assert job["result_count"] == 0
    assert "conflicting names" in job["error"]
    assert db.query_market_data(collector.COLLECTOR_NAME, limit=10) == []


@pytest.mark.asyncio
async def test_transient_timeout_retries_the_same_page(monkeypatch):
    monkeypatch.setenv("NAVER_KIS_CANARY_RETRY_DELAY_SEC", "0.1")
    with respx.mock(assert_all_called=True) as mock:
        kospi = mock.get(
            collector.MARKET_VALUE_URL.format(market="KOSPI"),
            params={"page": 1, "pageSize": collector.PAGE_SIZE},
        ).mock(
            side_effect=[
                httpx.ReadTimeout("slow Naver response"),
                httpx.Response(
                    200,
                    json={"totalCount": 1, "stocks": [_stock("005930")]},
                ),
            ]
        )
        _mock_market_page(mock, "KOSDAQ", 1, 1, [_stock("035720")])

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

    assert kospi.call_count == 2
    assert job["status"] == "completed"
    assert job["result_count"] == 2


@pytest.mark.asyncio
async def test_empty_second_market_fails_without_storing_first_market():
    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(mock, "KOSPI", 1, 1, [_stock("005930")])
        _mock_market_page(mock, "KOSDAQ", 1, 0, [])

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

    assert job["status"] == "failed"
    assert job["result_count"] == 0
    assert "KOSDAQ" in job["error"]
    assert "empty" in job["error"]
    assert db.query_market_data(collector.COLLECTOR_NAME, limit=10) == []


@pytest.mark.asyncio
async def test_all_invalid_rows_fail_current_price_contract_atomically():
    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(
            mock,
            "KOSPI",
            1,
            2,
            [
                _stock("005930", price="NaN", volume="100"),
                _stock("000660", price="0", volume="100"),
            ],
        )
        _mock_market_page(
            mock,
            "KOSDAQ",
            1,
            2,
            [
                _stock("035720", price="-1", volume="100"),
                _stock("035420", price="1", volume="Infinity"),
            ],
        )

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

    assert job["status"] == "failed"
    assert job["result_count"] == 0
    assert "current_price" in job["error"]
    assert db.query_market_data(collector.COLLECTOR_NAME, limit=10) == []


@pytest.mark.asyncio
async def test_late_upstream_shape_change_is_atomic():
    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(mock, "KOSPI", 1, 1, [_stock("005930")])
        mock.get(
            collector.MARKET_VALUE_URL.format(market="KOSDAQ"),
            params={"page": 1, "pageSize": collector.PAGE_SIZE},
        ).mock(return_value=httpx.Response(200, json={"stocks": [_stock("035720")]}))

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

    assert job["status"] == "failed"
    assert job["result_count"] == 0
    assert "totalCount" in job["error"]
    assert db.query_market_data(collector.COLLECTOR_NAME, limit=10) == []


@pytest.mark.asyncio
async def test_one_row_pagination_drift_is_bounded_and_accepted():
    kospi_first_page = [_stock("005930", volume="100")]
    kospi_first_page.extend(
        _stock(f"{300000 + index:06d}", stock_end_type="etf") for index in range(99)
    )
    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(mock, "KOSPI", 1, 101, kospi_first_page)
        _mock_market_page(mock, "KOSPI", 2, 101, [])
        _mock_market_page(
            mock,
            "KOSDAQ",
            1,
            1,
            [_stock("035720", volume="200")],
        )

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

    assert job["status"] == "completed"
    assert job["result_count"] == 2
    assert {
        row["symbol"] for row in db.query_market_data(collector.COLLECTOR_NAME)
    } == {
        "005930",
        "035720",
    }


@pytest.mark.asyncio
async def test_excessive_pagination_drift_still_fails_closed():
    kospi_first_page = [_stock("005930", volume="100")]
    kospi_first_page.extend(
        _stock(f"{300000 + index:06d}", stock_end_type="etf") for index in range(99)
    )
    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(mock, "KOSPI", 1, 110, kospi_first_page)
        _mock_market_page(mock, "KOSPI", 2, 110, [])

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

    assert job["status"] == "failed"
    assert job["result_count"] == 0
    assert "allowed drift" in job["error"]
    assert db.query_market_data(collector.COLLECTOR_NAME, limit=10) == []


@pytest.mark.asyncio
async def test_manual_symbols_are_rejected_without_http_requests():
    with respx.mock(assert_all_called=True):
        job = await run_collector(
            collector.COLLECTOR_NAME,
            collector.collect,
            ["005930"],
        )

    assert job["status"] == "failed"
    assert job["result_count"] == 0
    assert "rejects manual symbols" in job["error"]


@pytest.mark.asyncio
async def test_total_count_over_max_pages_fails_after_one_bounded_request():
    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(mock, "KOSPI", 1, 5_001, [])

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

        assert len(mock.calls) == 1

    assert job["status"] == "failed"
    assert job["result_count"] == 0
    assert "requires 51 pages" in job["error"]
    assert "maximum is 50" in job["error"]
    assert db.query_market_data(collector.COLLECTOR_NAME, limit=10) == []


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("999", 10.0), ("-1", 0.0), ("NaN", 1.25), ("invalid", 1.25)],
)
def test_request_spacing_environment_is_bounded(monkeypatch, raw_value, expected):
    monkeypatch.setenv("STOCK_REQUEST_DELAY_SEC", raw_value)

    assert collector._bounded_spacing("STOCK_REQUEST_DELAY_SEC", 1.25) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("top_n", ["0", "501", "not-an-integer"])
async def test_top_n_configuration_fails_closed_before_http(monkeypatch, top_n):
    monkeypatch.setenv("NAVER_KIS_CANARY_UNIVERSE_TOP_N", top_n)

    with respx.mock(assert_all_called=True):
        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

    assert job["status"] == "failed"
    assert job["result_count"] == 0
    assert "NAVER_KIS_CANARY_UNIVERSE_TOP_N" in job["error"]


@pytest.mark.asyncio
async def test_legacy_naver_policy_does_not_filter_dedicated_universe():
    db.upsert_collection_policy(
        "naver_stocks",
        include_symbols=["005930"],
        exclude_symbols=["035720"],
        include_fields=["change"],
    )

    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(mock, "KOSPI", 1, 1, [_stock("005930", volume="100")])
        _mock_market_page(mock, "KOSDAQ", 1, 1, [_stock("035720", volume="200")])

        job = await run_collector(collector.COLLECTOR_NAME, collector.collect)

    assert job["status"] == "completed"
    assert job["result_count"] == 2
    assert db.get_collection_policy(collector.COLLECTOR_NAME) is None
    assert db.get_collection_policy("naver_stocks")["exclude_symbols"] == ["035720"]
    rows = db.query_market_data(collector.COLLECTOR_NAME, limit=10)
    assert {row["symbol"] for row in rows} == {"005930", "035720"}
    assert all(row["payload"]["current_price"] == 9900.0 for row in rows)


def test_rest_registry_runs_dedicated_collector():
    assert app_main.COLLECTORS[collector.COLLECTOR_NAME] is collector.collect

    with respx.mock(assert_all_called=True) as mock:
        _mock_market_page(mock, "KOSPI", 1, 1, [_stock("005930", volume="100")])
        _mock_market_page(mock, "KOSDAQ", 1, 1, [_stock("035720", volume="200")])

        client = TestClient(app_main.app, raise_server_exceptions=False)
        try:
            response = client.post(
                "/collect/sync",
                json={"collector": collector.COLLECTOR_NAME},
            )
        finally:
            client.close()

    assert response.status_code == 200
    job = response.json()
    assert job["collector"] == collector.COLLECTOR_NAME
    assert job["status"] == "completed"
    assert job["result_count"] == 2

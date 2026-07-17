from datetime import datetime, timezone

import pytest

from app.collectors import exchange_1m


def test_last_timestamp_accepts_postgres_aware_datetime(monkeypatch):
    timestamp = datetime(2026, 7, 11, 10, 25, tzinfo=timezone.utc)
    monkeypatch.setattr(
        exchange_1m.db,
        "query_market_candles",
        lambda *_args, **_kwargs: [{"candle_time": timestamp}],
    )

    assert exchange_1m._last_ts_ms_from_db("upbit_1m", "BTC/KRW") == int(
        timestamp.timestamp() * 1000
    )


def test_last_timestamp_keeps_legacy_naive_kst_contract(monkeypatch):
    monkeypatch.setattr(
        exchange_1m.db,
        "query_market_candles",
        lambda *_args, **_kwargs: [{"candle_time": "2026-07-11 19:25:00"}],
    )

    expected = datetime(2026, 7, 11, 19, 25, tzinfo=exchange_1m.KST)
    assert exchange_1m._last_ts_ms_from_db("upbit_1m", "BTC/KRW") == int(
        expected.timestamp() * 1000
    )


def test_rotation_batch_prioritizes_missing_then_oldest(monkeypatch):
    monkeypatch.setattr(
        exchange_1m.db,
        "collection_symbol_attempt_times",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        exchange_1m.db,
        "latest_candle_times",
        lambda *_args: {
            "ETH/KRW": datetime(2026, 7, 17, 5, 30, tzinfo=timezone.utc),
            "XRP/KRW": datetime(2026, 7, 17, 5, 20, tzinfo=timezone.utc),
        },
    )
    selected = exchange_1m.select_rotation_batch(
        "upbit_1m",
        ["XRP/KRW", "BTC/KRW", "ETH/KRW"],
        2,
    )

    assert selected == ["BTC/KRW", "XRP/KRW"]


@pytest.mark.asyncio
async def test_rotation_collects_only_bounded_batch_with_bulk_upsert(monkeypatch):
    class FakeExchange:
        pass

    inserted: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        exchange_1m,
        "get_krw_market_symbols",
        lambda *_args: ["XRP/KRW", "BTC/KRW", "ETH/KRW"],
    )
    monkeypatch.setattr(
        exchange_1m.db,
        "latest_candle_times",
        lambda *_args: {
            "ETH/KRW": datetime(2026, 7, 17, 5, 30, tzinfo=timezone.utc),
        },
    )
    monkeypatch.setattr(
        exchange_1m.db,
        "collection_symbol_attempt_times",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        exchange_1m,
        "_fetch_1m_paginated",
        lambda *_args, **_kwargs: [[1_752_729_600_000, 1, 2, 0.5, 1.5, 3]],
    )
    monkeypatch.setattr(exchange_1m, "_last_ts_ms_from_db", lambda *_args: None)

    def fake_insert(job_id, collector, symbol, interval, values):
        assert job_id == "job-1"
        assert interval == "1m"
        inserted.append((collector, symbol, len(values)))
        return len(values)

    monkeypatch.setattr(exchange_1m.db, "insert_market_candles", fake_insert)
    monkeypatch.setattr(
        exchange_1m.db,
        "mark_collection_symbol_attempt",
        lambda *_args, **_kwargs: None,
    )

    count = await exchange_1m.collect_krw_1m(
        "job-1",
        exchanges=["upbit"],
        batch_size=2,
        ex_objs={"upbit": FakeExchange()},
    )

    assert count == 2
    assert inserted == [
        ("upbit_1m", "BTC/KRW", 1),
        ("upbit_1m", "XRP/KRW", 1),
    ]

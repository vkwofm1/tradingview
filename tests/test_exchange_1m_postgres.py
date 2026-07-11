from datetime import datetime, timezone

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

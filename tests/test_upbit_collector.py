from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.collectors import upbit


@pytest.fixture
def sleep(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(upbit.asyncio, "sleep", mock)
    return mock


@pytest.mark.asyncio
async def test_retry_429_then_persist_once_and_deduplicate(sleep, monkeypatch):
    inserted = []
    monkeypatch.setattr(upbit.db, "insert_market_candle", lambda *args: inserted.append(args))
    with respx.mock as router:
        route = router.get(upbit.UPBIT_URL).mock(side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json=[{"candle_date_time_kst": "2026-09-10T09:00:00"}]),
        ])
        assert await upbit.collect("job", ["btc", "KRW-BTC", " "]) == 1
        assert route.call_count == 2
    assert len(inserted) == 1
    assert [call.args[0] for call in sleep.call_args_list] == [0.2, 2.0, 0.2]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,headers,calls", [
    (429, {}, 4), (418, {}, 1), (400, {}, 1),
    (429, {"Retry-After": "60"}, 1),
    (429, {"Retry-After": "NaN"}, 1),
    (429, {"Retry-After": "invalid"}, 1),
])
async def test_retries_are_bounded_and_never_retry_bans(sleep, status, headers, calls):
    with respx.mock as router:
        route = router.get(upbit.UPBIT_URL).respond(status, headers=headers)
        with pytest.raises(httpx.HTTPStatusError):
            await upbit.collect("job", ["BTC"])
        assert route.call_count == calls


@pytest.mark.asyncio
async def test_waits_for_exhausted_remaining_quota(sleep):
    with respx.mock as router:
        router.get(upbit.UPBIT_URL).respond(
            200, headers={"Remaining-Req": "group=candle; min=1800; sec=0"}, json=[],
        )
        assert await upbit.collect("job", ["BTC"]) == 0
    assert [call.args[0] for call in sleep.call_args_list] == [0.2, 1.0]

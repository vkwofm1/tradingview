"""Upbit KRW market collector using the public 1-minute candles REST API."""

import asyncio
import math

import httpx

from app import db

UPBIT_URL = "https://api.upbit.com/v1/candles/minutes/1"

DEFAULT_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]
CANDLE_COUNT = 60
MAX_ATTEMPTS = 4
REQUEST_INTERVAL = 0.2
MAX_RETRY_WAIT = 30.0


async def _fetch_candles(client: httpx.AsyncClient, market: str) -> list[dict]:
    """공유 IP의 candle 제한에 맞춰 조회만 제한적으로 재시도한다."""
    for attempt in range(MAX_ATTEMPTS):
        await asyncio.sleep(REQUEST_INTERVAL)
        response = await client.get(UPBIT_URL, params={"market": market, "count": CANDLE_COUNT})
        if response.status_code == 429 and attempt < MAX_ATTEMPTS - 1:
            delay = float(attempt + 1)
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    requested_delay = float(retry_after)
                except ValueError:
                    response.raise_for_status()
                if not math.isfinite(requested_delay) or requested_delay > MAX_RETRY_WAIT:
                    response.raise_for_status()
                delay = max(delay, requested_delay)
            await asyncio.sleep(delay)
            continue
        response.raise_for_status()  # 418 차단과 영구 오류는 재시도하지 않는다.
        remaining = response.headers.get("Remaining-Req", "")
        if any(part.strip() == "sec=0" for part in remaining.split(";")):
            await asyncio.sleep(1.0)
        return response.json()
    raise RuntimeError("upbit_candle_retry_exhausted")


def _normalize_market(symbol: str) -> str:
    value = (symbol or "").strip().upper()
    if not value:
        return value
    if "-" in value:
        return value
    return f"KRW-{value}"


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    raw_markets = symbols or DEFAULT_MARKETS
    markets = list(dict.fromkeys(market for symbol in raw_markets if (market := _normalize_market(symbol))))
    count = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for market in markets:
            rows = await _fetch_candles(client, market)
            for row in rows:
                candle_time = row.get("candle_date_time_kst") or row.get("candle_date_time_utc")
                if not candle_time:
                    continue
                db.insert_market_candle(job_id, "upbit", market, "1m", candle_time, row)
                count += 1
    return count

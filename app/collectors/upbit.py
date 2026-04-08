"""Upbit KRW market collector using the public 1-minute candles REST API."""

import httpx

from app import db

UPBIT_URL = "https://api.upbit.com/v1/candles/minutes/1"

DEFAULT_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]
CANDLE_COUNT = 60


def _normalize_market(symbol: str) -> str:
    value = (symbol or "").strip().upper()
    if not value:
        return value
    if "-" in value:
        return value
    return f"KRW-{value}"


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    raw_markets = symbols or DEFAULT_MARKETS
    markets = [_normalize_market(symbol) for symbol in raw_markets]
    count = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for market in markets:
            resp = await client.get(UPBIT_URL, params={"market": market, "count": CANDLE_COUNT})
            resp.raise_for_status()
            rows = resp.json()
            for row in rows:
                candle_time = row.get("candle_date_time_kst") or row.get("candle_date_time_utc")
                if not candle_time:
                    continue
                db.insert_market_candle(job_id, "upbit", market, "1m", candle_time, row)
                count += 1
    return count

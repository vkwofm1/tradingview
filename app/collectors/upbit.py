"""Upbit KRW market collector using the public ticker REST API."""

import httpx

from app import db

UPBIT_URL = "https://api.upbit.com/v1/ticker"

DEFAULT_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    markets = symbols or DEFAULT_MARKETS
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(UPBIT_URL, params={"markets": ",".join(markets)})
        resp.raise_for_status()
        rows = resp.json()

    count = 0
    for row in rows:
        market = row.get("market") or row.get("code") or "UNKNOWN"
        db.insert_market_data(job_id, "upbit", market, row)
        count += 1
    return count

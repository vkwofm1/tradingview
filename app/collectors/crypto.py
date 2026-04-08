"""Crypto market data collector using CoinGecko free API."""

import httpx

from app import db

COINGECKO_URL = "https://api.coingecko.com/api/v3"

DEFAULT_IDS = ["bitcoin", "ethereum", "solana", "cardano", "dogecoin"]


async def collect(job_id: str, ids: list[str] | None = None) -> int:
    ids = ids or DEFAULT_IDS
    params = {
        "ids": ",".join(ids),
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_market_cap": "true",
        "include_24hr_vol": "true",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{COINGECKO_URL}/simple/price", params=params)
        resp.raise_for_status()
        data = resp.json()

    count = 0
    for coin_id, info in data.items():
        db.insert_market_data(job_id, "crypto", coin_id.upper(), info)
        count += 1
    return count

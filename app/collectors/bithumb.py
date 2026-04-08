"""Bithumb KRW public ticker collector."""

import httpx

from app import db

BITHUMB_URL = "https://api.bithumb.com/public/ticker/ALL_KRW"

DEFAULT_SYMBOLS = ["BTC", "ETH", "XRP", "SOL", "DOGE"]


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    wanted = {s.upper() for s in (symbols or DEFAULT_SYMBOLS)}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(BITHUMB_URL)
        resp.raise_for_status()
        body = resp.json()

    if body.get("status") != "0000":
        raise RuntimeError(f"Bithumb API error: {body.get('status')}")

    data = body.get("data", {})
    feed_date = data.get("date")

    count = 0
    for sym, info in data.items():
        if sym == "date":
            continue
        if sym.upper() not in wanted:
            continue
        if not isinstance(info, dict):
            continue
        payload = {
            "symbol": sym,
            "closing_price": info.get("closing_price"),
            "opening_price": info.get("opening_price"),
            "max_price": info.get("max_price"),
            "min_price": info.get("min_price"),
            "units_traded_24H": info.get("units_traded_24H"),
            "acc_trade_value_24H": info.get("acc_trade_value_24H"),
            "fluctate_rate_24H": info.get("fluctate_rate_24H"),
            "fluctate_24H": info.get("fluctate_24H"),
            "date": feed_date,
        }
        db.insert_market_data(job_id, "bithumb", sym.upper(), payload)
        count += 1
    return count

"""Bithumb KRW public 1-minute candle collector."""

from datetime import datetime, timezone

import httpx

from app import db

BITHUMB_URL = "https://api.bithumb.com/public/candlestick/{symbol}_KRW/1m"

DEFAULT_SYMBOLS = ["BTC", "ETH", "XRP", "SOL", "DOGE"]


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    wanted = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
    count = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for sym in wanted:
            resp = await client.get(BITHUMB_URL.format(symbol=sym))
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") != "0000":
                raise RuntimeError(f"Bithumb API error: {body.get('status')}")
            for row in body.get("data", [])[-60:]:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                ts_ms, open_p, close_p, high_p, low_p, volume = row[:6]
                candle_time = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).isoformat()
                payload = {
                    "symbol": sym,
                    "open": open_p,
                    "close": close_p,
                    "high": high_p,
                    "low": low_p,
                    "volume": volume,
                    "timestamp": ts_ms,
                }
                db.insert_market_candle(job_id, "bithumb", sym, "1m", candle_time, payload)
                count += 1
    return count

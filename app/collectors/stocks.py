"""Stock market data collector using Yahoo Finance v8 chart API (no key required)."""

import httpx

from app import db

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

DEFAULT_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    symbols = symbols or DEFAULT_SYMBOLS
    count = 0
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "tradingview-crawl/0.1"},
    ) as client:
        for sym in symbols:
            resp = await client.get(
                f"{YF_URL}/{sym}",
                params={"range": "1d", "interval": "5m"},
            )
            resp.raise_for_status()
            chart = resp.json().get("chart", {}).get("result", [{}])[0]
            meta = chart.get("meta", {})
            payload = {
                "symbol": meta.get("symbol", sym),
                "currency": meta.get("currency"),
                "regularMarketPrice": meta.get("regularMarketPrice"),
                "previousClose": meta.get("previousClose"),
                "exchangeName": meta.get("exchangeName"),
            }
            db.insert_market_data(job_id, "stocks", sym.upper(), payload)
            count += 1
    return count

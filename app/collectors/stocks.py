"""Stock market data collector using Yahoo Finance v8 chart API (no key required)."""

import asyncio
import os
import random

import httpx

from app import db

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

DEFAULT_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
_DEFAULT_REQUEST_DELAY_SEC = 1.25
_DEFAULT_REQUEST_JITTER_SEC = 0.75


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


async def _request_spacing(delay_sec: float, jitter_sec: float) -> None:
    wait = delay_sec + (random.uniform(0, jitter_sec) if jitter_sec else 0.0)
    if wait > 0:
        await asyncio.sleep(wait)


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    symbols = symbols or DEFAULT_SYMBOLS
    count = 0
    delay_sec = _env_float("STOCK_REQUEST_DELAY_SEC", _DEFAULT_REQUEST_DELAY_SEC)
    jitter_sec = _env_float("STOCK_REQUEST_JITTER_SEC", _DEFAULT_REQUEST_JITTER_SEC)
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "tradingview-crawl/0.1"},
    ) as client:
        for index, sym in enumerate(symbols):
            if index > 0:
                await _request_spacing(delay_sec, jitter_sec)
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
            filtered = db.apply_collection_policy("stocks", sym, payload)
            if filtered is None:
                continue
            db.insert_market_data(job_id, "stocks", sym.upper(), filtered)
            count += 1
    return count

"""Korean stocks collector via Naver Finance mobile JSON API.

Strategy:
- If `symbols` is provided, fetch each code's `/integration` quote.
- Otherwise, fetch the KOSPI top-100 ranking by market cap and pull each one.
- Concurrency is bounded by a semaphore to avoid hammering Naver.
"""

import asyncio

import httpx

from app import db

RANKING_URL = "https://m.stock.naver.com/api/stocks/marketValue/KOSPI"
QUOTE_URL = "https://m.stock.naver.com/api/stock/{code}/integration"

# Fallback if the ranking endpoint is unreachable.
FALLBACK_SYMBOLS = ["005930", "000660", "035420", "035720", "005380"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json",
}

_CONCURRENCY = 8


async def _fetch_top_kospi(client: httpx.AsyncClient, page_size: int = 100) -> list[str]:
    """Return KOSPI top-N stock codes by market cap."""
    try:
        resp = await client.get(
            RANKING_URL, params={"page": 1, "pageSize": page_size}
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception:
        return list(FALLBACK_SYMBOLS)

    # Naver mobile API returns either a list or {"stocks": [...]} depending on shape.
    items = body if isinstance(body, list) else body.get("stocks") or body.get("data") or []
    codes: list[str] = []
    for item in items:
        code = item.get("itemCode") or item.get("code") or item.get("symbolCode")
        if code:
            codes.append(str(code))
    return codes or list(FALLBACK_SYMBOLS)


async def _fetch_quote(client: httpx.AsyncClient, code: str) -> dict:
    """Fetch single-stock quote payload."""
    resp = await client.get(QUOTE_URL.format(code=code))
    resp.raise_for_status()
    data = resp.json()

    exchange = data.get("stockExchangeType") or {}
    if not isinstance(exchange, dict):
        exchange = {}

    return {
        "name": data.get("stockName"),
        "code": code,
        "current_price": data.get("closePrice"),
        "change": data.get("compareToPreviousClosePrice"),
        "change_rate": data.get("fluctuationsRatio"),
        "volume": data.get("accumulatedTradingVolume"),
        "market": exchange.get("code") or exchange.get("name"),
        "trade_date": data.get("localTradedAt"),
    }


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        if symbols:
            codes = [str(s) for s in symbols]
        else:
            codes = await _fetch_top_kospi(client, page_size=100)

        sem = asyncio.Semaphore(_CONCURRENCY)
        results: list[tuple[str, dict | None]] = []

        async def _one(code: str) -> None:
            async with sem:
                try:
                    payload = await _fetch_quote(client, code)
                    results.append((code, payload))
                except Exception as exc:
                    results.append((code, {"error": str(exc), "code": code}))

        await asyncio.gather(*(_one(c) for c in codes))

    count = 0
    for code, payload in results:
        if payload is None:
            continue
        db.insert_market_data(job_id, "naver_stocks", code, payload)
        count += 1
    return count

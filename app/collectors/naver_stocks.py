"""Korean stocks collector via Naver Finance mobile JSON API.

Strategy:
- If `symbols` is provided, fetch each code's basic quote.
- Otherwise, fetch the KOSPI top-100 ranking by market cap and pull each one.
- Concurrency is bounded by a semaphore to avoid hammering Naver.
"""

import asyncio
import logging

import httpx

from app import db

RANKING_URL = "https://m.stock.naver.com/api/stocks/marketValue/KOSPI"
# polling.finance.naver.com/api/realtime was returning 406; switched to the
# mobile integration endpoint which accepts the same headers.
QUOTE_URL = "https://m.stock.naver.com/api/stock/{code}/integration"

# Fallback if the ranking endpoint is unreachable.
FALLBACK_SYMBOLS = ["005930", "000660", "035420", "035720", "005380"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://m.stock.naver.com",
}

_CONCURRENCY = 8
log = logging.getLogger(__name__)


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


def _safe_float(val: str | int | float | None) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


async def _fetch_quote(client: httpx.AsyncClient, code: str) -> dict | None:
    """Fetch single-stock quote from the mobile integration endpoint."""
    resp = await client.get(QUOTE_URL.format(code=code))
    resp.raise_for_status()
    body = resp.json()

    # Unwrap common envelope structures
    if isinstance(body, dict):
        inner = body.get("data") or body.get("result") or body.get("stock") or body
    else:
        log.warning("naver_stocks: unexpected response type %s for %s", type(body).__name__, code)
        return None

    raw_price = (
        inner.get("closePrice")
        or inner.get("currentPrice")
        or inner.get("nv")
        or inner.get("stockPrice")
        or inner.get("price")
    )
    if raw_price is None:
        log.warning(
            "naver_stocks: no price field for %s (keys=%s)",
            code, list(body.keys())[:8],
        )
        return None

    current_price = _safe_float(raw_price)
    if current_price is None:
        return None

    return {
        "name": inner.get("stockName") or inner.get("name") or inner.get("nm"),
        "code": code,
        "current_price": current_price,
        "change": _safe_float(inner.get("compareToPreviousClosePrice") or inner.get("cv")),
        "change_rate": _safe_float(inner.get("fluctuationsRatio") or inner.get("cr")),
        "volume": _safe_float(inner.get("accumulatedTradingVolume") or inner.get("aq")),
        "market": "KRX",
        "trade_date": inner.get("localTradedAt") or inner.get("tradeDate") or inner.get("ms"),
    }


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    errors: list[str] = []
    missing_price: list[str] = []

    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        if symbols:
            codes = [str(s) for s in symbols]
        else:
            codes = await _fetch_top_kospi(client, page_size=100)

        sem = asyncio.Semaphore(_CONCURRENCY)
        results: list[tuple[str, dict]] = []

        async def _one(code: str) -> None:
            async with sem:
                try:
                    payload = await _fetch_quote(client, code)
                    if payload is not None:
                        results.append((code, payload))
                    else:
                        missing_price.append(code)
                except Exception as exc:
                    errors.append(f"{code}: {exc}")
                    log.warning("naver_stocks: failed to fetch %s: %s", code, exc)

        await asyncio.gather(*(_one(c) for c in codes))

    if not results and codes:
        if errors:
            raise RuntimeError(
                f"naver_stocks: 0/{len(codes)} succeeded. "
                f"errors (first 2): {errors[:2]}"
            )
        raise RuntimeError(
            f"naver_stocks: 0/{len(codes)} succeeded. "
            f"no price field found for any symbol (first 2: {missing_price[:2]}). "
            f"Check QUOTE_URL={QUOTE_URL!r} and response structure."
        )

    count = 0
    for code, payload in results:
        db.insert_market_data(job_id, "naver_stocks", code, payload)
        count += 1
    return count

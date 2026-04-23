"""Korean stocks collector via Naver Finance mobile JSON API.

Strategy:
- If `symbols` is provided, fetch each code's quote directly.
- Otherwise, paginate through KOSPI **and** KOSDAQ market-value rankings to
  collect the full universe of listed stocks (코스피 + 코스닥 전체 상장 종목).
- Concurrency is bounded by a semaphore to avoid hammering Naver.
"""

import asyncio
import logging

import httpx

from app import db

log = logging.getLogger(__name__)

RANKING_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
QUOTE_URL = (
    "https://polling.finance.naver.com/api/realtime"
    "?query=SERVICE_ITEM:{code}|SERVICE_RECENT_ITEM:{code}&_callback="
)

MARKETS = ["KOSPI", "KOSDAQ"]
PAGE_SIZE = 100  # Naver API max per page

# Fallback if all ranking endpoints are unreachable.
FALLBACK_SYMBOLS = [
    ("005930", "KOSPI"),
    ("000660", "KOSPI"),
    ("035420", "KOSPI"),
    ("035720", "KOSDAQ"),
    ("005380", "KOSPI"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json",
}

_CONCURRENCY = 20  # raised for full-universe collection (~2 300 stocks)


async def _fetch_market_symbols(
    client: httpx.AsyncClient, market: str
) -> list[tuple[str, str]]:
    """Return all (code, market) pairs for one market via pagination."""
    url = RANKING_URL.format(market=market)
    results: list[tuple[str, str]] = []
    page = 1
    while True:
        try:
            resp = await client.get(url, params={"page": page, "pageSize": PAGE_SIZE})
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            log.warning("Naver ranking fetch failed (market=%s page=%d): %s", market, page, exc)
            break

        # API returns either a list or {"stocks": [...]}
        items = (
            body
            if isinstance(body, list)
            else body.get("stocks") or body.get("data") or []
        )
        if not items:
            break  # no more pages

        for item in items:
            code = item.get("itemCode") or item.get("code") or item.get("symbolCode")
            if code:
                results.append((str(code), market))

        # If fewer items than PAGE_SIZE were returned we are on the last page.
        if len(items) < PAGE_SIZE:
            break
        page += 1

    log.info("Naver %s: %d종목 수집 (pages=%d)", market, len(results), page)
    return results


async def _fetch_all_symbols(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    """Collect the full listed universe across all configured markets."""
    tasks = [_fetch_market_symbols(client, m) for m in MARKETS]
    per_market = await asyncio.gather(*tasks)
    combined: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pairs in per_market:
        for code, market in pairs:
            if code not in seen:
                seen.add(code)
                combined.append((code, market))

    if not combined:
        log.warning("전체 종목 조회 실패, fallback 사용")
        return list(FALLBACK_SYMBOLS)

    return combined


async def _fetch_quote(client: httpx.AsyncClient, code: str, market: str) -> dict | None:
    """Fetch single-stock quote payload."""
    try:
        resp = await client.get(QUOTE_URL.format(code=code))
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        log.debug("Quote fetch failed (%s): %s", code, exc)
        return None

    result = body.get("result") or {}
    areas = result.get("areas") or []
    item = None
    for area in areas:
        datas = area.get("datas") or []
        if datas:
            item = datas[0]
            break

    if not item:
        return None

    current_price = item.get("nv")
    if current_price is None:
        return None

    return {
        "name": item.get("nm"),
        "code": code,
        "current_price": current_price,
        "change": item.get("cv"),
        "change_rate": item.get("cr"),
        "volume": item.get("aq"),
        "market": market,
        "trade_date": item.get("ms"),
    }


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        if symbols:
            # Manual symbol list — market label unknown, use "KRX" as default.
            pairs: list[tuple[str, str]] = [(str(s), "KRX") for s in symbols]
        else:
            pairs = await _fetch_all_symbols(client)

        log.info("naver_stocks: 총 %d종목 현재가 조회 시작", len(pairs))

        sem = asyncio.Semaphore(_CONCURRENCY)
        results: list[tuple[str, dict | None]] = []

        async def _one(code: str, market: str) -> None:
            async with sem:
                payload = await _fetch_quote(client, code, market)
                if payload is not None:
                    results.append((code, payload))

        await asyncio.gather(*(_one(c, m) for c, m in pairs))

    count = 0
    for code, payload in results:
        if payload is None:
            continue
        db.insert_market_data(job_id, "naver_stocks", code, payload)
        count += 1

    log.info("naver_stocks: %d/%d종목 저장 완료", count, len(pairs))
    return count
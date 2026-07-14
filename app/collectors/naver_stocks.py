"""Korean stocks collector via Naver Finance mobile JSON API.

Strategy:
- If `symbols` is provided, fetch each code's basic quote.
- Otherwise, fetch the KOSPI top-100 ranking by market cap and pull each one.
- Concurrency is bounded by a semaphore to avoid hammering Naver.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random

import httpx

from app import db

RANKING_URL = "https://m.stock.naver.com/api/stocks/marketValue/KOSPI"
# The integration endpoint keeps fundamentals in nested sections and no longer
# exposes the quote fields consistently. The basic endpoint has stable quote
# keys used by the collector.
QUOTE_URL = "https://m.stock.naver.com/api/stock/{code}/basic"

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

_DEFAULT_CONCURRENCY = 1
_DEFAULT_REQUEST_DELAY_SEC = 1.25
_DEFAULT_REQUEST_JITTER_SEC = 0.75
log = logging.getLogger(__name__)


class NaverPriceContractError(RuntimeError):
    """Raised when no Naver quote satisfies the required price contract."""


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


async def _request_spacing(delay_sec: float, jitter_sec: float) -> None:
    wait = delay_sec + (random.uniform(0, jitter_sec) if jitter_sec else 0.0)
    if wait > 0:
        await asyncio.sleep(wait)


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


def _safe_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _valid_current_price(val: object) -> float | None:
    """Return a normalized finite positive price, or None when invalid."""
    if isinstance(val, bool):
        return None
    price = _safe_float(val)
    if price is None or not math.isfinite(price) or price <= 0:
        return None
    return price


async def _fetch_quote(
    client: httpx.AsyncClient,
    code: str,
) -> dict[str, object] | None:
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

    total_infos = {}
    for item in inner.get("totalInfos") or []:
        if isinstance(item, dict) and item.get("code"):
            total_infos[item["code"]] = item.get("value")

    raw_price = next(
        (
            value
            for value in (
                inner.get("closePrice"),
                inner.get("currentPrice"),
                inner.get("nv"),
                inner.get("stockPrice"),
                inner.get("price"),
                total_infos.get("closePrice"),
                total_infos.get("nowPrice"),
            )
            if value is not None
        ),
        None,
    )
    if raw_price is None:
        log.warning(
            "naver_stocks: no price field for %s (keys=%s)",
            code, list(body.keys())[:8],
        )
        return None

    current_price = _valid_current_price(raw_price)
    if current_price is None:
        log.warning("naver_stocks: invalid current price for %s", code)
        return None

    return {
        "name": inner.get("stockName") or inner.get("name") or inner.get("nm"),
        "code": code,
        "current_price": current_price,
        "change": _safe_float(inner.get("compareToPreviousClosePrice") or inner.get("cv")),
        "change_rate": _safe_float(inner.get("fluctuationsRatio") or inner.get("cr")),
        "volume": _safe_float(inner.get("accumulatedTradingVolume") or inner.get("aq") or total_infos.get("accumulatedTradingVolume")),
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

        concurrency = _env_int("NAVER_STOCKS_CONCURRENCY", _DEFAULT_CONCURRENCY)
        delay_sec = _env_float("STOCK_REQUEST_DELAY_SEC", _DEFAULT_REQUEST_DELAY_SEC)
        jitter_sec = _env_float("STOCK_REQUEST_JITTER_SEC", _DEFAULT_REQUEST_JITTER_SEC)
        sem = asyncio.Semaphore(concurrency)
        spacing_lock = asyncio.Lock()
        results: list[tuple[str, dict[str, object]]] = []

        async def _one(index: int, code: str) -> None:
            async with sem:
                async with spacing_lock:
                    if index > 0:
                        await _request_spacing(delay_sec, jitter_sec)
                try:
                    payload = await _fetch_quote(client, code)
                    if payload is not None:
                        results.append((code, payload))
                    else:
                        missing_price.append(code)
                except Exception as exc:
                    errors.append(f"{code}: {exc}")
                    log.warning("naver_stocks: failed to fetch %s: %s", code, exc)

        await asyncio.gather(*(_one(index, code) for index, code in enumerate(codes)))

    if not results and codes:
        if errors and not missing_price:
            raise RuntimeError(
                f"naver_stocks: 0/{len(codes)} succeeded. "
                f"errors (first 2): {errors[:2]}"
            )
        raise NaverPriceContractError(
            f"naver_stocks: 0/{len(codes)} succeeded. "
            "no valid positive finite current_price found for any symbol "
            f"(first 2: {missing_price[:2]}). "
            f"Check QUOTE_URL={QUOTE_URL!r} and response structure."
        )

    prepared: list[tuple[str, dict[str, object]]] = []
    policy_approved_count = 0
    invalid_after_policy: list[str] = []
    for code, payload in results:
        filtered = db.apply_collection_policy("naver_stocks", code, payload)
        if filtered is None:
            continue
        policy_approved_count += 1
        current_price = _valid_current_price(filtered.get("current_price"))
        if current_price is None:
            invalid_after_policy.append(code)
            continue
        normalized = dict(filtered)
        normalized["current_price"] = current_price
        prepared.append((code, normalized))

    if policy_approved_count and not prepared:
        raise NaverPriceContractError(
            "naver_stocks: collection policy removed or invalidated "
            f"current_price for all {policy_approved_count} approved rows "
            f"(first 2: {invalid_after_policy[:2]})"
        )

    for code, payload in prepared:
        db.insert_market_data(job_id, "naver_stocks", code, payload)
    return len(prepared)

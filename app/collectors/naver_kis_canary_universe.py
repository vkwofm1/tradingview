"""Build the low-price KIS canary universe from Naver market rankings.

The collector intentionally uses only the two paginated market-value endpoints.
It never fans out into per-symbol quote requests and is isolated from the legacy
``naver_stocks`` meeting collection policy.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import re
from dataclasses import dataclass

import httpx

from app import db

COLLECTOR_NAME = "naver_kis_canary_universe"
MARKETS = ("KOSPI", "KOSDAQ")
MARKET_VALUE_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
PAGE_SIZE = 100
MAX_PAGES_PER_MARKET = 50
DEFAULT_TOP_N = 200
MAX_TOP_N = 500

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

_DEFAULT_REQUEST_DELAY_SEC = 1.25
_DEFAULT_REQUEST_JITTER_SEC = 0.75
_MAX_REQUEST_SPACING_SEC = 10.0
_CODE_RE = re.compile(r"\d{6}")
log = logging.getLogger(__name__)


class NaverKisCanaryUniverseError(RuntimeError):
    """Base error for a failed canary-universe snapshot."""


class NaverKisCanaryUniverseConfigurationError(NaverKisCanaryUniverseError):
    """Raised when a local collector setting violates its bounds."""


class NaverKisCanaryUniverseManualSymbolsError(NaverKisCanaryUniverseError):
    """Raised when a caller tries to replace the full-market universe."""


class NaverKisCanaryUniverseUpstreamError(NaverKisCanaryUniverseError):
    """Raised when Naver cannot return a usable HTTP/JSON response."""


class NaverKisCanaryUniverseShapeError(NaverKisCanaryUniverseError):
    """Raised when a Naver response no longer matches the pagination contract."""


class NaverKisCanaryUniverseBoundsError(NaverKisCanaryUniverseError):
    """Raised before an upstream or local collection bound can be exceeded."""


class NaverKisCanaryUniverseValidationError(NaverKisCanaryUniverseError):
    """Raised when no complete, eligible universe can be stored safely."""


@dataclass(frozen=True)
class _RawItem:
    market: str
    page: int
    total_count: int
    value: dict[str, object]


def _finite_number(value: object) -> float | None:
    """Return a finite float for scalar numeric input, otherwise ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded_spacing(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        log.warning("%s: invalid %s=%r; using %.2f", COLLECTOR_NAME, name, raw_value, default)
        return default
    if not math.isfinite(value):
        log.warning("%s: non-finite %s=%r; using %.2f", COLLECTOR_NAME, name, raw_value, default)
        return default
    return min(_MAX_REQUEST_SPACING_SEC, max(0.0, value))


def _top_n() -> int:
    raw_value = os.environ.get("NAVER_KIS_CANARY_UNIVERSE_TOP_N")
    if raw_value is None:
        return DEFAULT_TOP_N
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise NaverKisCanaryUniverseConfigurationError(
            "NAVER_KIS_CANARY_UNIVERSE_TOP_N must be an integer"
        ) from exc
    if not 1 <= value <= MAX_TOP_N:
        raise NaverKisCanaryUniverseBoundsError(
            "NAVER_KIS_CANARY_UNIVERSE_TOP_N must be between "
            f"1 and {MAX_TOP_N}; got {value}"
        )
    return value


def _total_count(body: dict[str, object], market: str, page: int) -> int:
    raw_value = body.get("totalCount")
    if isinstance(raw_value, bool):
        raw_value = None
    try:
        value = int(str(raw_value))
    except (TypeError, ValueError) as exc:
        raise NaverKisCanaryUniverseShapeError(
            f"{market} page {page}: totalCount must be a non-negative integer"
        ) from exc
    if value < 0:
        raise NaverKisCanaryUniverseShapeError(
            f"{market} page {page}: totalCount must be non-negative; got {value}"
        )
    return value


async def _request_spacing(delay_sec: float, jitter_sec: float) -> None:
    wait = delay_sec + (random.uniform(0.0, jitter_sec) if jitter_sec else 0.0)
    if wait > 0:
        await asyncio.sleep(wait)


async def _fetch_page(
    client: httpx.AsyncClient,
    market: str,
    page: int,
) -> dict[str, object]:
    url = MARKET_VALUE_URL.format(market=market)
    try:
        response = await client.get(
            url,
            params={"page": page, "pageSize": PAGE_SIZE},
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        raise NaverKisCanaryUniverseUpstreamError(
            f"{market} page {page}: Naver request failed: {type(exc).__name__}"
        ) from exc
    except ValueError as exc:
        raise NaverKisCanaryUniverseShapeError(
            f"{market} page {page}: response is not valid JSON"
        ) from exc
    if not isinstance(body, dict):
        raise NaverKisCanaryUniverseShapeError(
            f"{market} page {page}: response root must be an object"
        )
    return body


async def _fetch_market(
    client: httpx.AsyncClient,
    market: str,
    delay_sec: float,
    jitter_sec: float,
) -> list[_RawItem]:
    expected_total: int | None = None
    expected_pages: int | None = None
    rows: list[_RawItem] = []

    for page in range(1, MAX_PAGES_PER_MARKET + 1):
        if page > 1:
            await _request_spacing(delay_sec, jitter_sec)
        body = await _fetch_page(client, market, page)
        total_count = _total_count(body, market, page)

        stocks = body.get("stocks")
        if not isinstance(stocks, list):
            raise NaverKisCanaryUniverseShapeError(
                f"{market} page {page}: stocks must be a list"
            )
        if len(stocks) > PAGE_SIZE:
            raise NaverKisCanaryUniverseBoundsError(
                f"{market} page {page}: received {len(stocks)} rows; "
                f"pageSize bound is {PAGE_SIZE}"
            )

        if expected_total is None:
            expected_total = total_count
            expected_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
            if expected_pages > MAX_PAGES_PER_MARKET:
                raise NaverKisCanaryUniverseBoundsError(
                    f"{market}: totalCount={total_count} requires {expected_pages} pages; "
                    f"maximum is {MAX_PAGES_PER_MARKET}"
                )
        elif total_count != expected_total:
            raise NaverKisCanaryUniverseShapeError(
                f"{market} page {page}: totalCount changed from "
                f"{expected_total} to {total_count} during pagination"
            )

        for index, item in enumerate(stocks):
            if not isinstance(item, dict):
                raise NaverKisCanaryUniverseShapeError(
                    f"{market} page {page} row {index}: stock must be an object"
                )
            rows.append(
                _RawItem(
                    market=market,
                    page=page,
                    total_count=total_count,
                    value=item,
                )
            )

        if expected_pages is not None and page >= expected_pages:
            break

    if expected_total is None or expected_total == 0:
        raise NaverKisCanaryUniverseValidationError(
            f"{market}: Naver universe is empty"
        )
    if len(rows) != expected_total:
        raise NaverKisCanaryUniverseShapeError(
            f"{market}: pagination returned {len(rows)} rows for "
            f"totalCount={expected_total}"
        )
    return rows


def _required_text(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _prepare_item(raw: _RawItem) -> tuple[dict[str, object] | None, str | None]:
    item = raw.value
    code = _required_text(item, "itemCode")
    if code is None or _CODE_RE.fullmatch(code) is None:
        return None, "invalid_itemCode"

    stock_end_type = _required_text(item, "stockEndType")
    if stock_end_type is None or stock_end_type.lower() != "stock":
        return None, "not_common_stock"

    price_field = "closePriceRaw"
    price = _finite_number(item.get(price_field))
    if price is None:
        price_field = "currentPrice"
        price = _finite_number(item.get(price_field))
    if price is None or not 0 < price <= 10_000:
        return None, "invalid_current_price"

    volume = _finite_number(item.get("accumulatedTradingVolumeRaw"))
    if volume is None or volume <= 0:
        return None, "invalid_volume"

    as_of = _required_text(item, "localTradedAt")
    if as_of is None:
        return None, "missing_localTradedAt"
    market_status = _required_text(item, "marketStatus")
    if market_status is None:
        return None, "missing_marketStatus"

    payload: dict[str, object] = {
        "code": code,
        "name": _required_text(item, "stockName")
        or _required_text(item, "itemName")
        or _required_text(item, "name"),
        "current_price": price,
        "volume": volume,
        "market": raw.market,
        "as_of": as_of,
        "market_status": market_status,
        "source": "naver_market_value",
        "provenance": {
            "endpoint": MARKET_VALUE_URL.format(market=raw.market),
            "page": raw.page,
            "page_size": PAGE_SIZE,
            "total_count": raw.total_count,
            "price_field": price_field,
            "volume_field": "accumulatedTradingVolumeRaw",
        },
    }
    return payload, None


def _prepare_universe(raw_rows: list[_RawItem], top_n: int) -> list[dict[str, object]]:
    eligible: list[dict[str, object]] = []
    invalid_reasons: dict[str, int] = {}
    seen_codes: set[str] = set()

    for raw in raw_rows:
        payload, reason = _prepare_item(raw)
        if payload is None:
            assert reason is not None
            invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1
            continue
        code = str(payload["code"])
        if code in seen_codes:
            raise NaverKisCanaryUniverseValidationError(
                f"duplicate itemCode across market pages: {code}"
            )
        seen_codes.add(code)
        eligible.append(payload)

    if not eligible:
        reason_summary = ", ".join(
            f"{reason}={count}" for reason, count in sorted(invalid_reasons.items())
        )
        raise NaverKisCanaryUniverseValidationError(
            "no eligible six-digit common stocks with finite "
            f"0<current_price<=10000 and volume>0 ({reason_summary or 'no rows'})"
        )

    eligible.sort(key=lambda row: (-float(row["volume"]), str(row["code"])))
    selected = eligible[:top_n]
    for rank, payload in enumerate(selected, start=1):
        payload["volume_rank"] = rank

    for payload in selected:
        price = _finite_number(payload.get("current_price"))
        volume = _finite_number(payload.get("volume"))
        if price is None or not 0 < price <= 10_000 or volume is None or volume <= 0:
            raise NaverKisCanaryUniverseValidationError(
                "prepared universe violated the current_price/volume contract"
            )
    return selected


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    """Collect and atomically validate the full KOSPI/KOSDAQ canary universe."""
    if symbols:
        raise NaverKisCanaryUniverseManualSymbolsError(
            f"{COLLECTOR_NAME} rejects manual symbols; received {len(symbols)}"
        )

    top_n = _top_n()
    delay_sec = _bounded_spacing(
        "STOCK_REQUEST_DELAY_SEC", _DEFAULT_REQUEST_DELAY_SEC
    )
    jitter_sec = _bounded_spacing(
        "STOCK_REQUEST_JITTER_SEC", _DEFAULT_REQUEST_JITTER_SEC
    )

    raw_rows: list[_RawItem] = []
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        for market_index, market in enumerate(MARKETS):
            if market_index > 0:
                await _request_spacing(delay_sec, jitter_sec)
            raw_rows.extend(
                await _fetch_market(client, market, delay_sec, jitter_sec)
            )

    prepared = _prepare_universe(raw_rows, top_n)
    for payload in prepared:
        code = str(payload["code"])
        db.insert_market_data(job_id, COLLECTOR_NAME, code, payload)
    return len(prepared)

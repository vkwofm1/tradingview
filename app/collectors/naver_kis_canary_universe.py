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
# KOSPI/KOSDAQ 각 50페이지 x 100행이 수집 상한이다. TOP_N 환경값은
# 부분 universe를 만드는 선택 개수가 아니라 전체 수집의 안전 상한으로만 쓴다.
# 따라서 운영 기본값은 페이지네이션 최대치와 같고, 적격 종목이 이 값을 넘으면
# 조용히 잘라내지 않고 fail-closed 한다.
DEFAULT_TOP_N = len(MARKETS) * PAGE_SIZE * MAX_PAGES_PER_MARKET
MAX_TOP_N = DEFAULT_TOP_N

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
_DEFAULT_REQUEST_TIMEOUT_SEC = 45.0
_MAX_REQUEST_TIMEOUT_SEC = 120.0
_DEFAULT_COLLECTION_TIMEOUT_SEC = 480.0
_MAX_COLLECTION_TIMEOUT_SEC = 570.0
_DEFAULT_REQUEST_ATTEMPTS = 3
_MAX_REQUEST_ATTEMPTS = 5
_DEFAULT_RETRY_DELAY_SEC = 1.0
_MAX_RETRY_DELAY_SEC = 10.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_PAGINATION_DRIFT_RATIO = 0.002
_MAX_PAGINATION_DRIFT_ROWS = 5
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
        log.warning(
            "%s: invalid %s=%r; using %.2f", COLLECTOR_NAME, name, raw_value, default
        )
        return default
    if not math.isfinite(value):
        log.warning(
            "%s: non-finite %s=%r; using %.2f", COLLECTOR_NAME, name, raw_value, default
        )
        return default
    return min(_MAX_REQUEST_SPACING_SEC, max(0.0, value))


def _bounded_positive_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        log.warning(
            "%s: invalid %s=%r; using %.2f", COLLECTOR_NAME, name, raw_value, default
        )
        return default
    if not math.isfinite(value):
        log.warning(
            "%s: non-finite %s=%r; using %.2f",
            COLLECTOR_NAME,
            name,
            raw_value,
            default,
        )
        return default
    return min(maximum, max(minimum, value))


def _request_attempts() -> int:
    raw_value = os.environ.get("NAVER_KIS_CANARY_REQUEST_ATTEMPTS")
    if raw_value is None:
        return _DEFAULT_REQUEST_ATTEMPTS
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise NaverKisCanaryUniverseConfigurationError(
            "NAVER_KIS_CANARY_REQUEST_ATTEMPTS must be an integer"
        ) from exc
    if not 1 <= value <= _MAX_REQUEST_ATTEMPTS:
        raise NaverKisCanaryUniverseBoundsError(
            "NAVER_KIS_CANARY_REQUEST_ATTEMPTS must be between "
            f"1 and {_MAX_REQUEST_ATTEMPTS}; got {value}"
        )
    return value


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


def _allowed_pagination_drift(total_count: int) -> int:
    return max(
        1,
        min(
            _MAX_PAGINATION_DRIFT_ROWS,
            math.ceil(total_count * _MAX_PAGINATION_DRIFT_RATIO),
        ),
    )


async def _request_spacing(delay_sec: float, jitter_sec: float) -> None:
    wait = delay_sec + (random.uniform(0.0, jitter_sec) if jitter_sec else 0.0)
    if wait > 0:
        await asyncio.sleep(wait)


async def _fetch_page(
    client: httpx.AsyncClient,
    market: str,
    page: int,
    attempts: int,
    retry_delay_sec: float,
) -> dict[str, object]:
    url = MARKET_VALUE_URL.format(market=market)
    last_error: httpx.HTTPError | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.get(
                url,
                params={"page": page, "pageSize": PAGE_SIZE},
            )
            response.raise_for_status()
            body = response.json()
            break
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code not in _RETRYABLE_STATUS_CODES:
                raise NaverKisCanaryUniverseUpstreamError(
                    f"{market} page {page}: Naver request failed: "
                    f"HTTP {exc.response.status_code}"
                ) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
        except ValueError as exc:
            raise NaverKisCanaryUniverseShapeError(
                f"{market} page {page}: response is not valid JSON"
            ) from exc

        if attempt >= attempts:
            assert last_error is not None
            raise NaverKisCanaryUniverseUpstreamError(
                f"{market} page {page}: Naver request failed after "
                f"{attempts} attempts: {type(last_error).__name__}"
            ) from last_error
        wait = retry_delay_sec * attempt
        log.warning(
            "%s: retrying %s page %d after %s (attempt %d/%d, wait %.1fs)",
            COLLECTOR_NAME,
            market,
            page,
            type(last_error).__name__,
            attempt,
            attempts,
            wait,
        )
        if wait > 0:
            await asyncio.sleep(wait)
    else:  # pragma: no cover - the loop always returns or raises
        raise AssertionError("unreachable request retry state")

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
    attempts: int,
    retry_delay_sec: float,
) -> list[_RawItem]:
    expected_total: int | None = None
    expected_pages: int | None = None
    rows: list[_RawItem] = []

    for page in range(1, MAX_PAGES_PER_MARKET + 1):
        if page > 1:
            await _request_spacing(delay_sec, jitter_sec)
        body = await _fetch_page(
            client,
            market,
            page,
            attempts,
            retry_delay_sec,
        )
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
    row_delta = len(rows) - expected_total
    allowed_drift = _allowed_pagination_drift(expected_total)
    if abs(row_delta) > allowed_drift:
        raise NaverKisCanaryUniverseShapeError(
            f"{market}: pagination returned {len(rows)} rows for "
            f"totalCount={expected_total}; allowed drift is {allowed_drift}"
        )
    if row_delta:
        log.warning(
            "%s: accepting bounded %s pagination drift: rows=%d, "
            "totalCount=%d, delta=%+d, allowed=%d",
            COLLECTOR_NAME,
            market,
            len(rows),
            expected_total,
            row_delta,
            allowed_drift,
        )
    return rows


def _required_text(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _reported_market(item: dict[str, object]) -> tuple[str | None, bool]:
    """Return Naver's embedded exchange identity and whether sources conflict."""
    identities: set[str] = set()
    exchange_type = item.get("stockExchangeType")
    if isinstance(exchange_type, dict):
        for key in ("nameEng", "name", "code"):
            value = str(exchange_type.get(key) or "").strip().upper()
            if value in {"KOSPI", "KS"}:
                identities.add("KOSPI")
            elif value in {"KOSDAQ", "KQ"}:
                identities.add("KOSDAQ")
    sosok = str(item.get("sosok") or "").strip()
    if sosok == "0":
        identities.add("KOSPI")
    elif sosok == "1":
        identities.add("KOSDAQ")
    if len(identities) > 1:
        return None, True
    return (next(iter(identities)) if identities else None), False


def _prepare_item(raw: _RawItem) -> tuple[dict[str, object] | None, str | None]:
    item = raw.value
    code = _required_text(item, "itemCode")
    if code is None or _CODE_RE.fullmatch(code) is None:
        return None, "invalid_itemCode"
    # KRX 보통주 본주는 0으로 끝나는 6자리 단축코드만 허용한다. 우선주 등은
    # Naver의 stockEndType이 stock이어도 canary universe에 섞지 않는다.
    if not code.endswith("0"):
        return None, "not_common_stock_code"

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

    reported_market, market_conflict = _reported_market(item)
    if market_conflict:
        return None, "conflicting_market_identity"
    market = reported_market or raw.market

    payload: dict[str, object] = {
        "code": code,
        "name": _required_text(item, "stockName")
        or _required_text(item, "itemName")
        or _required_text(item, "name"),
        "current_price": price,
        "volume": volume,
        "market": market,
        "as_of": as_of,
        "market_status": market_status,
        "source": "naver_market_value",
        "provenance": {
            "endpoint": MARKET_VALUE_URL.format(market=raw.market),
            "requested_market": raw.market,
            "reported_market": reported_market,
            "page": raw.page,
            "page_size": PAGE_SIZE,
            "total_count": raw.total_count,
            "price_field": price_field,
            "volume_field": "accumulatedTradingVolumeRaw",
        },
    }
    return payload, None


def _merge_duplicate(
    current: dict[str, object],
    candidate: dict[str, object],
) -> dict[str, object]:
    code = str(current["code"])
    current_name = str(current.get("name") or "").strip()
    candidate_name = str(candidate.get("name") or "").strip()
    if current_name and candidate_name and current_name != candidate_name:
        raise NaverKisCanaryUniverseValidationError(
            f"duplicate itemCode has conflicting names: {code}"
        )
    if current.get("market") != candidate.get("market"):
        raise NaverKisCanaryUniverseValidationError(
            f"duplicate itemCode has conflicting markets: {code}"
        )

    def preference(payload: dict[str, object]) -> tuple[str, float, float, str]:
        provenance = payload.get("provenance")
        page = ""
        if isinstance(provenance, dict):
            page = f"{int(provenance.get('page') or 0):04d}"
        return (
            str(payload.get("as_of") or ""),
            float(payload.get("volume") or 0),
            float(payload.get("current_price") or 0),
            page,
        )

    chosen = dict(max((current, candidate), key=preference))
    sources: list[dict[str, object]] = []
    for payload in (current, candidate):
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict):
            continue
        prior_sources = provenance.get("duplicate_sources")
        if isinstance(prior_sources, list):
            sources.extend(
                source for source in prior_sources if isinstance(source, dict)
            )
        else:
            sources.append(
                {
                    "requested_market": provenance.get("requested_market"),
                    "page": provenance.get("page"),
                }
            )

    provenance = dict(chosen.get("provenance") or {})
    provenance["duplicates_collapsed"] = max(0, len(sources) - 1)
    provenance["duplicate_sources"] = sources
    chosen["provenance"] = provenance
    return chosen


def _prepare_universe(raw_rows: list[_RawItem], top_n: int) -> list[dict[str, object]]:
    eligible_by_code: dict[str, dict[str, object]] = {}
    invalid_reasons: dict[str, int] = {}

    for raw in raw_rows:
        payload, reason = _prepare_item(raw)
        if payload is None:
            assert reason is not None
            invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1
            continue
        code = str(payload["code"])
        current = eligible_by_code.get(code)
        eligible_by_code[code] = (
            _merge_duplicate(current, payload) if current is not None else payload
        )

    if not eligible_by_code:
        reason_summary = ", ".join(
            f"{reason}={count}" for reason, count in sorted(invalid_reasons.items())
        )
        raise NaverKisCanaryUniverseValidationError(
            "no eligible six-digit common stocks with finite "
            f"0<current_price<=10000 and volume>0 ({reason_summary or 'no rows'})"
        )

    eligible = list(eligible_by_code.values())
    eligible.sort(key=lambda row: (-float(row["volume"]), str(row["code"])))
    if len(eligible) > top_n:
        raise NaverKisCanaryUniverseBoundsError(
            "eligible universe exceeds configured safety bound; "
            f"eligible={len(eligible)}, bound={top_n}"
        )
    for rank, payload in enumerate(eligible, start=1):
        payload["volume_rank"] = rank

    for payload in eligible:
        price = _finite_number(payload.get("current_price"))
        volume = _finite_number(payload.get("volume"))
        if price is None or not 0 < price <= 10_000 or volume is None or volume <= 0:
            raise NaverKisCanaryUniverseValidationError(
                "prepared universe violated the current_price/volume contract"
            )
    return eligible


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    """Collect and atomically validate the full KOSPI/KOSDAQ canary universe."""
    if symbols:
        raise NaverKisCanaryUniverseManualSymbolsError(
            f"{COLLECTOR_NAME} rejects manual symbols; received {len(symbols)}"
        )

    top_n = _top_n()
    delay_sec = _bounded_spacing("STOCK_REQUEST_DELAY_SEC", _DEFAULT_REQUEST_DELAY_SEC)
    jitter_sec = _bounded_spacing(
        "STOCK_REQUEST_JITTER_SEC", _DEFAULT_REQUEST_JITTER_SEC
    )
    request_timeout_sec = _bounded_positive_float(
        "NAVER_KIS_CANARY_REQUEST_TIMEOUT_SEC",
        _DEFAULT_REQUEST_TIMEOUT_SEC,
        minimum=1.0,
        maximum=_MAX_REQUEST_TIMEOUT_SEC,
    )
    collection_timeout_sec = _bounded_positive_float(
        "NAVER_KIS_CANARY_COLLECTION_TIMEOUT_SEC",
        _DEFAULT_COLLECTION_TIMEOUT_SEC,
        minimum=30.0,
        maximum=_MAX_COLLECTION_TIMEOUT_SEC,
    )
    attempts = _request_attempts()
    retry_delay_sec = _bounded_positive_float(
        "NAVER_KIS_CANARY_RETRY_DELAY_SEC",
        _DEFAULT_RETRY_DELAY_SEC,
        minimum=0.1,
        maximum=_MAX_RETRY_DELAY_SEC,
    )

    raw_rows: list[_RawItem] = []
    timeout = httpx.Timeout(
        connect=min(10.0, request_timeout_sec),
        read=request_timeout_sec,
        write=min(10.0, request_timeout_sec),
        pool=min(10.0, request_timeout_sec),
    )
    try:
        async with asyncio.timeout(collection_timeout_sec):
            async with httpx.AsyncClient(timeout=timeout, headers=HEADERS) as client:
                for market_index, market in enumerate(MARKETS):
                    if market_index > 0:
                        await _request_spacing(delay_sec, jitter_sec)
                    raw_rows.extend(
                        await _fetch_market(
                            client,
                            market,
                            delay_sec,
                            jitter_sec,
                            attempts,
                            retry_delay_sec,
                        )
                    )
    except TimeoutError as exc:
        raise NaverKisCanaryUniverseUpstreamError(
            "Naver universe collection deadline exceeded after "
            f"{collection_timeout_sec:.0f}s"
        ) from exc

    prepared = _prepare_universe(raw_rows, top_n)
    for payload in prepared:
        code = str(payload["code"])
        db.insert_market_data(job_id, COLLECTOR_NAME, code, payload)
    return len(prepared)

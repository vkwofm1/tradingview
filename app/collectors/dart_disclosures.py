"""Korean corporate disclosure collector via OpenDART list API.

The collector follows the disclosure-poller pattern documented in
gameworkerkim/vibe-investing: fetch today's OpenDART `list.json` once, then
filter locally by the collection policy/watchlist.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

import httpx

from app import db

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

HEADERS = {
    "User-Agent": os.environ.get(
        "DART_USER_AGENT",
        "tradingview-crawl/0.2 disclosure collector contact=operator@example.com",
    ),
    "Accept": "application/json",
}

log = logging.getLogger(__name__)


def _today_yyyymmdd() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")


def _api_key() -> str:
    return (
        os.environ.get("DART_API_KEY")
        or os.environ.get("OPENDART_API_KEY")
        or ""
    ).strip()


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _disclosure_date_range() -> tuple[str, str]:
    begin = os.environ.get("DART_DISCLOSURE_BEGIN_DATE", "").strip()
    end = os.environ.get("DART_DISCLOSURE_END_DATE", "").strip()
    today = _today_yyyymmdd()
    return begin or today, end or begin or today


def _classify_importance(report_name: str) -> tuple[str, str]:
    text = re.sub(r"\s+", "", report_name or "")
    high_patterns = [
        "전환사채권발행결정",
        "신주인수권부사채권발행결정",
        "최대주주변경",
        "감사의견거절",
        "의견거절",
        "한정의견",
        "회생절차",
        "상장폐지",
        "거래정지",
        "불성실공시",
        "횡령",
        "배임",
    ]
    medium_patterns = [
        "유상증자",
        "제3자배정",
        "자기주식",
        "타법인주식및출자증권취득결정",
        "타법인주식및출자증권처분결정",
        "주요사항보고서",
        "임원",
        "주요주주",
        "영업양수",
        "영업양도",
        "합병",
        "분할",
        "정정",
    ]
    if any(pattern in text for pattern in high_patterns):
        return "high", "risk_or_control_event"
    if any(pattern in text for pattern in medium_patterns):
        return "medium", "capital_structure_or_corporate_action"
    return "context", "general_disclosure"


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    rcept_no = str(item.get("rcept_no") or "").strip()
    stock_code = str(item.get("stock_code") or "").strip().upper()
    corp_code = str(item.get("corp_code") or "").strip()
    corp_name = str(item.get("corp_name") or "").strip()
    report_name = str(item.get("report_nm") or "").strip()
    rcept_dt = str(item.get("rcept_dt") or "").strip()
    importance, category = _classify_importance(report_name)
    symbol = stock_code or corp_code or corp_name
    return {
        "symbol": symbol,
        "corp_code": corp_code,
        "stock_code": stock_code,
        "corp_name": corp_name,
        "report_nm": report_name,
        "rcept_no": rcept_no,
        "rcept_dt": rcept_dt,
        "flr_nm": str(item.get("flr_nm") or "").strip(),
        "rm": str(item.get("rm") or "").strip(),
        "importance": importance,
        "category": category,
        "source": "opendart",
        "source_url": DART_VIEWER_URL.format(rcept_no=rcept_no) if rcept_no else "",
        "disclaimer": "공시 사실의 색인이며 평가나 투자 권유가 아닙니다.",
    }


async def _fetch_page(
    client: httpx.AsyncClient,
    api_key: str,
    begin_date: str,
    end_date: str,
    page_no: int,
    page_count: int,
) -> dict[str, Any]:
    params = {
        "crtfc_key": api_key,
        "bgn_de": begin_date,
        "end_de": end_date,
        "page_no": page_no,
        "page_count": page_count,
    }
    if disclosure_types := os.environ.get("DART_DISCLOSURE_TYPES", "").strip():
        params["pblntf_ty"] = disclosure_types
    resp = await client.get(LIST_URL, params=params)
    resp.raise_for_status()
    return resp.json()


def _is_success(body: dict[str, Any]) -> bool:
    return str(body.get("status") or "") == "000"


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    """Collect recent DART disclosures into market_data.

    `symbols` may contain stock codes, corp codes, or company names. If omitted,
    all disclosures returned by the date-range query are stored subject to the
    collection policy.
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("DART_API_KEY or OPENDART_API_KEY is required")

    begin_date, end_date = _disclosure_date_range()
    page_count = _env_int("DART_DISCLOSURE_PAGE_COUNT", 100, minimum=1, maximum=100)
    max_pages = _env_int("DART_DISCLOSURE_MAX_PAGES", 10, minimum=1, maximum=100)
    timeout = float(os.environ.get("DART_DISCLOSURE_TIMEOUT_SEC", "20"))
    request_delay = float(os.environ.get("DART_DISCLOSURE_REQUEST_DELAY_SEC", "0.15"))
    requested = {str(s).strip().upper() for s in symbols or [] if str(s).strip()}

    inserted = 0
    async with httpx.AsyncClient(timeout=timeout, headers=HEADERS) as client:
        for page_no in range(1, max_pages + 1):
            body = await _fetch_page(client, api_key, begin_date, end_date, page_no, page_count)
            if not _is_success(body):
                raise RuntimeError(
                    f"OpenDART list failed: status={body.get('status')} message={body.get('message')}"
                )

            items = body.get("list") or []
            if not isinstance(items, list) or not items:
                break

            for raw in items:
                if not isinstance(raw, dict):
                    continue
                payload = _normalize_item(raw)
                match_keys = {
                    str(payload.get("symbol") or "").upper(),
                    str(payload.get("stock_code") or "").upper(),
                    str(payload.get("corp_code") or "").upper(),
                    str(payload.get("corp_name") or "").upper(),
                }
                if requested and not (requested & match_keys):
                    continue
                symbol = str(payload.get("symbol") or "").upper()
                filtered = db.apply_collection_policy("dart_disclosures", symbol, payload)
                if filtered is None:
                    continue
                db.insert_market_data(job_id, "dart_disclosures", symbol, filtered)
                inserted += 1

            total_page = int(body.get("total_page") or page_no)
            if page_no >= total_page:
                break
            if request_delay > 0:
                await asyncio.sleep(request_delay)

    log.info(
        "dart_disclosures collected %s rows for %s~%s symbols=%s",
        inserted,
        begin_date,
        end_date,
        sorted(requested) if requested else "all",
    )
    return inserted

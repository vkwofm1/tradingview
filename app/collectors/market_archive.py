"""Market Archive 1분봉 조회 — 사외 archive에서 가져와 자체 DB에 미러.

aibitcoin 프로젝트의 market-archive 서비스(K8s service `market-archive:8510`)에서
빗썸/업비트/KIS 1분봉 데이터를 가져와 자체 `market_candles` 테이블에 저장한다.

invest-lead 회의 시점에 LLM이 MCP tool로 이를 호출하여 분단위 가격을 조회한다.
회의는 일봉 종가 기반이지만, 특정 종목의 미세 변동(돌발 갭, 분단위 슬리피지 등)을
확인하고 싶을 때 사용한다.

엔드포인트 (market_archive_api):
    GET  /api/health
    GET  /api/last_ts?exchange=&symbol=&timeframe=1m
    GET/POST /api/collect_until_now?exchange=&symbol=
    GET  /api/stats

Cluster DNS: http://market-archive.default.svc.cluster.local:8510
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx

from app import db

# market-archive K8s service. cluster.local 명시로 paperclip namespace에서도 접근.
DEFAULT_BASE_URL = os.environ.get(
    "MARKET_ARCHIVE_BASE_URL",
    "http://market-archive.default.svc.cluster.local:8510",
)
DEFAULT_TOKEN = os.environ.get("MARKET_ARCHIVE_API_TOKEN", "").strip()
DEFAULT_TIMEOUT = float(os.environ.get("MARKET_ARCHIVE_TIMEOUT", "15"))


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if DEFAULT_TOKEN:
        h["Authorization"] = f"Bearer {DEFAULT_TOKEN}"
    return h


async def collect(
    job_id: str,
    targets: list[dict] | list[str] | None = None,
    *,
    interval: str = "1m",
    base_url: str | None = None,
) -> int:
    """단일 종목 또는 종목 list를 market-archive에서 가져와 market_candles에 저장.

    Args:
        targets: 각 항목은
            - "EXCHANGE:SYMBOL" 문자열 (예: "bithumb:BTC/KRW", "kis:005930"), 또는
            - {"exchange": "...", "symbol": "...", "lookback_minutes": N} dict
        interval: 현재는 "1m"만 지원
    """
    url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    if not targets:
        return 0

    parsed: list[dict] = []
    for t in targets:
        if isinstance(t, str):
            if ":" in t:
                ex, sym = t.split(":", 1)
                parsed.append({"exchange": ex.strip().lower(), "symbol": sym.strip()})
        elif isinstance(t, dict):
            ex = (t.get("exchange") or "").lower()
            sym = t.get("symbol")
            if ex and sym:
                parsed.append({
                    "exchange": ex, "symbol": sym,
                    "lookback_minutes": t.get("lookback_minutes"),
                })

    if not parsed:
        return 0

    count = 0
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=_headers()) as client:
        for tgt in parsed:
            # 1) market-archive에 즉시 수집 요청 (gap fill)
            params = {"exchange": tgt["exchange"], "symbol": tgt["symbol"]}
            if tgt.get("lookback_minutes"):
                params["lookback_minutes_if_empty"] = str(tgt["lookback_minutes"])
            try:
                r = await client.post(f"{url}/api/collect_until_now", params=params)
                r.raise_for_status()
                collect_meta = r.json()
            except Exception as exc:
                collect_meta = {"ok": False, "error": str(exc)}

            # 2) 통계로 last_ts 확인 (간단 헬스)
            try:
                r2 = await client.get(
                    f"{url}/api/last_ts",
                    params={
                        "exchange": tgt["exchange"],
                        "symbol": tgt["symbol"],
                        "timeframe": interval,
                    },
                )
                r2.raise_for_status()
                last_ts_info = r2.json()
            except Exception as exc:
                last_ts_info = {"error": str(exc)}

            # 3) market_archive_api는 candles 자체를 반환하지 않으므로
            #    upstream에서 fetched_rows 메타만 저장한다 (분단위 raw는 archive DB에 있음).
            #    회의에서 LLM은 query_market_archive_status tool로 last_ts + 수집 결과만 확인.
            payload = {
                "exchange": tgt["exchange"],
                "symbol": tgt["symbol"],
                "interval": interval,
                "collect_result": collect_meta,
                "last_ts": last_ts_info,
                "queried_at": datetime.now(timezone.utc).isoformat(),
            }
            candle_time = datetime.now(timezone.utc).isoformat()
            try:
                db.insert_market_candle(
                    job_id,
                    "market_archive",
                    f"{tgt['exchange']}:{tgt['symbol']}",
                    interval,
                    candle_time,
                    payload,
                )
                count += 1
            except Exception:
                # UNIQUE 제약 등 — 무시하고 계속
                pass
    return count


async def fetch_last_ts(
    exchange: str, symbol: str, timeframe: str = "1m",
    *, base_url: str | None = None,
) -> dict:
    """단순 헬스 — DB에 쌓인 마지막 ts만 조회 (수집 trigger 없음)."""
    url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=_headers()) as client:
        r = await client.get(
            f"{url}/api/last_ts",
            params={"exchange": exchange.lower(), "symbol": symbol, "timeframe": timeframe},
        )
        r.raise_for_status()
        return r.json()


async def fetch_stats(*, base_url: str | None = None) -> dict:
    """전체 수집 통계 (거래소/타임프레임별 row 수, ts 범위)."""
    url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=_headers()) as client:
        r = await client.get(f"{url}/api/stats")
        r.raise_for_status()
        return r.json()

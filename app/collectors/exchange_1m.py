"""거래소 1분봉 통합 collector — 빗썸/업비트 KRW, KIS 국장, 미장.

invest-lead 회의 포트폴리오 선정 시 분단위 검증에 사용. paperclip의 LLM agent가
MCP tool 또는 HTTP API로 즉시/일일 수집을 trigger.

저장 위치: TradingView PostgreSQL (DATABASE_URL, market_candles 테이블).
schema: market_candles(collector, symbol, interval, candle_time, payload[JSON])
payload: {"open","high","low","close","volume","ts_ms"}

신규 상장 자동 포함:
    매 호출 ccxt.load_markets() / Wikipedia fetch / KIS API 호출이 fresh.

collector 명명:
    bithumb_1m, upbit_1m, us_stock_1m, kr_stock_1m
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from app import db

KST = timezone(timedelta(hours=9))

# 거래소별 ccxt 1m fetch 한도 (동적 검증됨)
_CCXT_1M_LIMITS = {"bithumb": 1000, "upbit": 200}
_CCXT_REQUEST_SPACING_SECONDS = {"bithumb": 0.08, "upbit": 0.12}
_KIS_CODE_RE = re.compile(r"^\d{6}$")

_WIKI_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; tradingview-bot/1.0)"}
_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_NDX100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _candles_to_payload(candle: list) -> dict:
    """ccxt OHLCV → payload."""
    ts_ms, o, h, l, c, v = candle[:6]
    return {"ts_ms": int(ts_ms), "open": o, "high": h, "low": l, "close": c, "volume": v}


def _candle_time_kst(ts_ms: int) -> str:
    return (
        datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        .astimezone(KST)
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def _last_ts_ms_from_db(collector: str, symbol: str, interval: str = "1m") -> int | None:
    """market_candles의 마지막 candle_time → epoch ms."""
    rows = db.query_market_candles(collector, symbol, interval, limit=1)
    if not rows:
        return None
    ct = rows[0].get("candle_time")
    if not ct:
        return None
    try:
        if isinstance(ct, datetime):
            dt = ct
        else:
            dt = datetime.fromisoformat(str(ct).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _insert_candles(job_id: str, collector: str, symbol: str, candles: list[list]) -> int:
    values = []
    for candle in candles:
        payload = _candles_to_payload(candle)
        values.append((_candle_time_kst(payload["ts_ms"]), payload))
    return db.insert_market_candles(job_id, collector, symbol, "1m", values)


# ───────────────────────────────────────────────────────────
# 빗썸/업비트 KRW 전체 1m
# ───────────────────────────────────────────────────────────

def get_krw_market_symbols(exchange_name: str, exchange: Any | None = None) -> list[str]:
    """ccxt로 빗썸/업비트 KRW active spot 페어 목록."""
    if exchange is None:
        import ccxt

        exchange = getattr(ccxt, exchange_name)({"enableRateLimit": True})
    ex = exchange
    try:
        markets = ex.load_markets()
    except Exception as e:
        print(f"[get_krw_market_symbols] {exchange_name}: {e}", flush=True)
        return []
    return sorted(
        sym for sym, m in markets.items()
        if (m.get("quote") or "").upper() == "KRW"
        and m.get("active", True)
        and (m.get("type") or "spot") == "spot"
    )


def _timestamp_sort_value(value: Any) -> float:
    if value is None:
        return float("-inf")
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return float("-inf")


def select_rotation_batch(
    collector: str,
    symbols: list[str],
    batch_size: int | None,
) -> list[str]:
    """Select missing/oldest symbols first without truncating the universe."""
    unique = sorted(dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if symbol))
    if batch_size is None or batch_size <= 0 or batch_size >= len(unique):
        return unique
    attempts = db.collection_symbol_attempt_times(collector)
    latest = {} if attempts else db.latest_candle_times(collector, "1m")
    return sorted(
        unique,
        key=lambda symbol: (
            _timestamp_sort_value(attempts.get(symbol)),
            _timestamp_sort_value(latest.get(symbol)),
            symbol,
        ),
    )[:batch_size]


def _fetch_1m_paginated(
    ex: Any, symbol: str, since_ms: int | None, *,
    limit: int, max_pages: int = 8, request_spacing_seconds: float = 0.0,
) -> list[list]:
    out: list[list] = []
    seen: set[int] = set()
    cursor = since_ms
    for _ in range(max_pages):
        page = None
        for attempt in range(4):
            try:
                page = (
                    ex.fetch_ohlcv(symbol, timeframe="1m", limit=limit)
                    if cursor is None
                    else ex.fetch_ohlcv(symbol, timeframe="1m", since=cursor, limit=limit)
                )
                break
            except Exception as exc:
                retryable = "429" in str(exc) or "too_many_requests" in str(exc).lower()
                if not retryable or attempt >= 3:
                    print(f"[fetch_1m_paginated] {symbol}: {exc}", flush=True)
                    return out
                time.sleep(max(request_spacing_seconds, 0.5 * (2 ** attempt)))
        if request_spacing_seconds > 0:
            time.sleep(request_spacing_seconds)
        if not page:
            break
        new = [c for c in page if c[0] not in seen]
        if not new:
            break
        for c in new:
            seen.add(c[0])
            out.append(c)
        last_ts = new[-1][0]
        next_cursor = last_ts + 60_000
        if cursor is not None and next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(page) < limit:
            break
    out.sort(key=lambda c: c[0])
    return out


async def collect_krw_1m(
    job_id: str, exchanges: list[str] | None = None,
    *, lookback_minutes: int = 4320, batch_size: int | None = None,
    ex_objs: dict | None = None,
) -> int:
    """빗썸/업비트 KRW 전체 1m. 사용 사례:
    매일 1회 cron + 즉시 수집 wrapper.
    """
    exchanges = exchanges or ["bithumb", "upbit"]
    total_rows = 0
    for ex_name in exchanges:
        if ex_name not in _CCXT_1M_LIMITS:
            continue
        ex = (ex_objs or {}).get(ex_name)
        if ex is None:
            import ccxt

            ex = getattr(ccxt, ex_name)({"enableRateLimit": True})
        symbols = get_krw_market_symbols(ex_name, ex)
        if not symbols:
            raise RuntimeError(f"{ex_name} active KRW spot universe is empty")
        collector = f"{ex_name}_1m"
        selected = select_rotation_batch(collector, symbols, batch_size)
        print(
            f"[collect_krw_1m] exchange={ex_name} universe={len(symbols)} "
            f"batch={len(selected)} collector={collector}",
            flush=True,
        )
        limit = _CCXT_1M_LIMITS[ex_name]
        for sym in selected:
            last_ms = _last_ts_ms_from_db(collector, sym)
            now_ms = int(time.time() * 1000)
            if last_ms is None:
                since_ms = now_ms - lookback_minutes * 60_000
            else:
                since_ms = max(last_ms + 60_000, now_ms - lookback_minutes * 60_000)
            max_pages = max(1, (now_ms - since_ms) // 60_000 // limit + 1)
            candles = _fetch_1m_paginated(
                ex,
                sym,
                since_ms,
                limit=limit,
                max_pages=max_pages,
                request_spacing_seconds=_CCXT_REQUEST_SPACING_SECONDS[ex_name],
            )
            total_rows += _insert_candles(job_id, collector, sym, candles)
            latest_candle = (
                datetime.fromtimestamp(candles[-1][0] / 1000, tz=timezone.utc)
                if candles
                else None
            )
            db.mark_collection_symbol_attempt(
                collector,
                sym,
                latest_candle_at=latest_candle,
                succeeded=bool(candles),
            )
    return total_rows


async def collect_krw_1m_until_now(
    job_id: str, targets: list[str] | None = None,
) -> int:
    """단일 거래소+티커 즉시 수집. targets=["bithumb:BTC/KRW","upbit:ETH/KRW"]."""
    import ccxt
    if not targets:
        return 0
    total_rows = 0
    ex_cache: dict = {}
    for t in targets:
        if ":" not in t:
            continue
        ex_name, sym = t.split(":", 1)
        ex_name = ex_name.strip().lower()
        sym = sym.strip()
        if ex_name not in _CCXT_1M_LIMITS:
            continue
        ex = ex_cache.get(ex_name) or getattr(ccxt, ex_name)({"enableRateLimit": True})
        ex_cache[ex_name] = ex
        collector = f"{ex_name}_1m"
        last_ms = _last_ts_ms_from_db(collector, sym)
        now_ms = int(time.time() * 1000)
        since_ms = (last_ms + 60_000) if last_ms is not None else (now_ms - 1440 * 60_000)
        max_minutes = (now_ms - since_ms) // 60_000 + 5
        limit = _CCXT_1M_LIMITS[ex_name]
        max_pages = max(1, int(max_minutes // limit) + 1)
        candles = _fetch_1m_paginated(ex, sym, since_ms, limit=limit, max_pages=max_pages)
        total_rows += _insert_candles(job_id, collector, sym, candles)
    return total_rows


# ───────────────────────────────────────────────────────────
# 미장 (yfinance + Wikipedia)
# ───────────────────────────────────────────────────────────

def get_us_universe(
    *, include_sp500: bool = True, include_ndx100: bool = True,
    extra_symbols: list[str] | None = None,
) -> list[str]:
    """S&P500 + NASDAQ-100 union + 추가 종목 (Wikipedia)."""
    import pandas as pd
    syms: set[str] = set()
    if include_sp500:
        try:
            req = urllib.request.Request(_SP500_URL, headers=_WIKI_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode("utf-8")
            tables = pd.read_html(io.StringIO(html))
            syms.update(str(s).strip().upper() for s in tables[0]["Symbol"].tolist())
        except Exception as e:
            print(f"[get_us_universe] sp500: {e}", flush=True)
    if include_ndx100:
        try:
            req = urllib.request.Request(_NDX100_URL, headers=_WIKI_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode("utf-8")
            tables = pd.read_html(io.StringIO(html))
            for t in tables[:8]:
                cols = list(t.columns)
                if "Ticker" in cols or "Symbol" in cols:
                    col = "Ticker" if "Ticker" in cols else "Symbol"
                    syms.update(str(s).strip().upper() for s in t[col].tolist())
                    break
        except Exception as e:
            print(f"[get_us_universe] ndx100: {e}", flush=True)
    if extra_symbols:
        syms.update(str(s).strip().upper() for s in extra_symbols if str(s).strip())
    return sorted(syms)


async def collect_us_stocks_1m(
    job_id: str, symbols: list[str] | None = None,
    *, batch_size: int = 50,
) -> int:
    """미장 universe 1m. yfinance batch download."""
    import yfinance as yf
    syms = symbols or get_us_universe()
    if not syms:
        return 0
    total_rows = 0
    for i in range(0, len(syms), batch_size):
        chunk = syms[i:i + batch_size]
        try:
            data = yf.download(
                tickers=chunk, period="2d", interval="1m",
                group_by="ticker", threads=True, progress=False, auto_adjust=False,
            )
        except Exception as e:
            print(f"[collect_us_stocks_1m] batch err: {e}", flush=True)
            continue
        for sym in chunk:
            try:
                df_sym = data if len(chunk) == 1 else (
                    data[sym] if sym in data.columns.get_level_values(0) else None
                )
                if df_sym is None or df_sym.empty:
                    continue
                df_sym = df_sym.dropna(how="all")
                if df_sym.empty:
                    continue
                if df_sym.index.tz is None:
                    df_sym.index = df_sym.index.tz_localize("America/New_York")
                last_ms = _last_ts_ms_from_db("us_stock_1m", sym)
                for ts_idx, row in df_sym.iterrows():
                    ts_ms = int(ts_idx.timestamp() * 1000)
                    if last_ms is not None and ts_ms <= last_ms:
                        continue
                    candle = [ts_ms, float(row.get("Open", 0)), float(row.get("High", 0)),
                              float(row.get("Low", 0)), float(row.get("Close", 0)),
                              float(row.get("Volume", 0))]
                    _insert_candle(job_id, "us_stock_1m", sym, candle)
                    total_rows += 1
            except Exception as e:
                print(f"[collect_us_stocks_1m] {sym}: {e}", flush=True)
    return total_rows


async def collect_us_stocks_1m_until_now(
    job_id: str, symbols: list[str] | None = None,
) -> int:
    """단일 미장 종목 즉시 수집."""
    import yfinance as yf
    if not symbols:
        return 0
    total_rows = 0
    for sym in symbols:
        try:
            last_ms = _last_ts_ms_from_db("us_stock_1m", sym)
            period = "1d" if last_ms is None else "2d"
            df = yf.Ticker(sym).history(period=period, interval="1m", auto_adjust=False)
            if df is None or df.empty:
                continue
            if df.index.tz is None:
                df.index = df.index.tz_localize("America/New_York")
            for ts_idx, row in df.iterrows():
                ts_ms = int(ts_idx.timestamp() * 1000)
                if last_ms is not None and ts_ms <= last_ms:
                    continue
                candle = [ts_ms, float(row.get("Open", 0)), float(row.get("High", 0)),
                          float(row.get("Low", 0)), float(row.get("Close", 0)),
                          float(row.get("Volume", 0))]
                _insert_candle(job_id, "us_stock_1m", sym, candle)
                total_rows += 1
        except Exception as e:
            print(f"[collect_us_stocks_1m_until_now] {sym}: {e}", flush=True)
    return total_rows


# ───────────────────────────────────────────────────────────
# 국장 (KIS API — 별도 KIS 컨테이너 또는 KIS secrets 필요)
# ───────────────────────────────────────────────────────────

def get_kr_stocks_universe(
    *, extra_symbols: list[str] | None = None,
) -> tuple[list[str], dict]:
    """국장 universe — env KIS_STOCK_UNIVERSE (콤마 구분) + 추가 종목 + 회의 결정.

    회의 결정 종목 추적은 별도 db (kis pod의 bitcoin_trades.db) 접근 필요 →
    여기서는 env + extra만. 회의 종목 자동 추적은 K8s CronJob env로 inject.
    """
    universe: set[str] = set()
    env = os.environ.get("KIS_STOCK_UNIVERSE", "").strip()
    sources: list[dict] = []
    if env:
        env_syms = [
            s.strip() for s in env.replace("|", ",").split(",")
            if s.strip() and _KIS_CODE_RE.match(s.strip())
        ]
        universe.update(env_syms)
        sources.append({"env": len(env_syms)})
    if extra_symbols:
        ext = [s.strip() for s in extra_symbols if s.strip() and _KIS_CODE_RE.match(s.strip())]
        universe.update(ext)
        sources.append({"extra": len(ext)})
    return sorted(universe), {"sources": sources, "total": len(universe)}


async def collect_kr_stocks_1m(
    job_id: str, symbols: list[str] | None = None,
    *, bars_per_call: int = 30, kis_client: Any = None,
) -> int:
    """국장 universe 1m — KIS API (KisClient 필요)."""
    if kis_client is None:
        try:
            from kis_client import KisClient, load_config  # type: ignore
            kis_client = KisClient(load_config(None))
        except Exception as e:
            print(f"[collect_kr_stocks_1m] kis_client init: {e}", flush=True)
            return 0
    if symbols is None:
        symbols, _ = get_kr_stocks_universe()
    if not symbols:
        return 0
    total_rows = 0
    for sym in symbols:
        try:
            df = kis_client.market_data.get_minute_ohlcv(sym, minutes=1, count=bars_per_call)
            if df is None or df.empty:
                continue
            for ts_idx, row in df.iterrows():
                ts_ms = int(ts_idx.replace(tzinfo=KST).timestamp() * 1000)
                last_ms = _last_ts_ms_from_db("kr_stock_1m", sym)
                if last_ms is not None and ts_ms <= last_ms:
                    continue
                candle = [ts_ms, row["open"], row["high"], row["low"], row["close"], row["volume"]]
                _insert_candle(job_id, "kr_stock_1m", sym, candle)
                total_rows += 1
        except Exception as e:
            print(f"[collect_kr_stocks_1m] {sym}: {e}", flush=True)
    return total_rows


async def collect_kr_stocks_1m_until_now(
    job_id: str, symbols: list[str] | None = None,
) -> int:
    """국장 단일 종목 즉시 수집 — KIS API."""
    return await collect_kr_stocks_1m(job_id, symbols)

"""미국 주식 현재가·완료 일봉/60분봉·거래량·재무 근거 수집."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import random
import re

import httpx

from app import db
from app.us_stock_market import (
    NY, aware, completed_candles, evidence_quality, financial_evidence_missing, number,
    recover_completed_candles, required_market_times,
)
from app.us_stock_research import cost_model, financial_metrics, price_plan, valuation

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
DEFAULT_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]


def configured_symbols():
    configured = os.environ.get("US_STOCK_SYMBOLS", "")
    return [
        s.strip().upper() for s in configured.split(",") if s.strip()
    ] or DEFAULT_SYMBOLS


def _env_float(name, default):
    value = number(os.environ.get(name, default))
    return max(0, value) if value is not None else default


async def _request_spacing(delay_sec, jitter_sec):
    await asyncio.sleep(
        delay_sec + (random.uniform(0, jitter_sec) if jitter_sec else 0)
    )


async def _chart(client, symbol, interval, period):
    for attempt in range(3):
        try:
            response = await client.get(
                f"{YF_URL}/{symbol}",
                params={"range": period, "interval": interval, "includePrePost": "false"},
            )
        except httpx.TransportError:
            if attempt == 2:
                raise
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if response.status_code in (429, 502, 503, 504) and attempt < 2:
            await asyncio.sleep(2 * (attempt + 1))
            continue
        response.raise_for_status()
        body = response.json().get("chart") or {}
        charts = body.get("result") or []
        if body.get("error") or not charts or not charts[0].get("timestamp"):
            raise ValueError(f"yahoo_{interval}_empty_or_error")
        if charts[0].get("meta", {}).get("symbol", "").upper() != symbol:
            raise ValueError("yahoo_symbol_mismatch")
        return charts[0]
    raise ValueError("yahoo_retry_exhausted")


def _fetch_financials(symbol, now):
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    info = ticker.get_info()
    if info.get("symbol", "").upper() != symbol:
        raise ValueError("financial_symbol_mismatch")
    return financial_metrics(
        info,
        ticker.get_income_stmt(),
        ticker.get_balance_sheet(),
        ticker.get_cash_flow(),
        now=now,
    )


async def _financials(symbol, now):
    previous = db.query_market_data("stocks", symbol, 1)
    cached = (previous[0]["payload"].get("fundamentals") or {}) if previous else {}
    fetched = aware(cached.get("fetched_at"))

    def usable(values):
        shares = number(values.get("shares_outstanding"))
        return (
            not financial_evidence_missing(values, now)
            and shares is not None and shares > 0
        )

    cache_valid = usable(cached)
    if cache_valid and now - fetched < timedelta(hours=24):
        return cached
    try:
        refreshed = await asyncio.wait_for(
            asyncio.to_thread(_fetch_financials, symbol, now), timeout=120
        )
        if usable(refreshed) or refreshed.get("reason") in {
            "currency_mismatch_or_unsupported",
            "financial_sector_requires_specialized_model",
        }:
            return refreshed
        refresh = {
            "status": "incomplete", "attempted_at": now.isoformat(),
            "missing": financial_evidence_missing(refreshed, now),
        }
        shares = number(refreshed.get("shares_outstanding"))
        if shares is None or shares <= 0:
            refresh["missing"].append("shares_outstanding")
    except Exception as exc:
        refresh = {
            "status": "failed", "attempted_at": now.isoformat(),
            "error": type(exc).__name__,
        }
        refreshed = {
            "source": "yahoo_finance_statements",
            "error": type(exc).__name__,
            "fetched_at": None,
        }
    # 24시간 갱신 실패는 기존 48시간 품질 기한 안에서만 복구한다.
    # 원래 조회 시각과 결산일을 보존하며, 만료·부적합 자료는 복구하지 않는다.
    return {**(cached if cache_valid else refreshed), "refresh": refresh}


async def collect(job_id: str, symbols: list[str] | None = None) -> int:
    # []는 정책에 의해 전부 제외된 것이므로 기본 종목을 되살리지 않는다.
    symbols = configured_symbols() if symbols is None else symbols
    symbols = list(dict.fromkeys(str(s).strip().upper() for s in symbols))
    if any(
        not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]{0,19}", symbol) for symbol in symbols
    ):
        raise ValueError("invalid_us_stock_symbol")
    snapshots, errors = [], []
    delay = _env_float("STOCK_REQUEST_DELAY_SEC", 1.25)
    jitter = _env_float("STOCK_REQUEST_JITTER_SEC", 0.75)
    async with httpx.AsyncClient(
        timeout=20, headers={"User-Agent": "tradingview-crawl/0.1"}
    ) as client:
        for symbol in symbols:
            try:
                daily_chart = await _chart(client, symbol, "1d", "6mo")
                await _request_spacing(delay, jitter)
                hourly_chart = await _chart(client, symbol, "60m", "1mo")
                now = datetime.now(timezone.utc)
                daily = completed_candles(daily_chart, "1d", now)
                hourly = completed_candles(hourly_chart, "60m", now)
                frames, recovered = {"1d": daily, "60m": hourly}, {}
                required = dict(zip(("1d", "60m"), required_market_times(now)))
                for interval, chart in (("1d", daily_chart), ("60m", hourly_chart)):
                    rows = frames[interval]
                    if len(rows) < 20 or aware(rows[-1]["end_at"]) < required[interval]:
                        cached = db.query_market_candles("stocks", symbol, interval, 200)
                        frames[interval], reused = recover_completed_candles(
                            rows, [row["payload"] for row in cached], chart, interval, now
                        )
                        if reused:
                            recovered[interval] = {
                                "source": "validated_stored_completed_candles",
                                "reused_start_at": reused,
                            }
                daily, hourly = frames["1d"], frames["60m"]
                meta = hourly_chart["meta"]
                price = number(meta.get("regularMarketPrice"))
                if (
                    not daily
                    or not hourly
                    or price is None
                    or price <= 0
                    or meta.get("currency") != "USD"
                ):
                    raise ValueError("required_us_price_candles_or_currency_missing")
                price_epoch = number(meta.get("regularMarketTime"))
                price_at = (
                    datetime.fromtimestamp(price_epoch, timezone.utc).isoformat()
                    if price_epoch
                    else None
                )
                fundamentals = await _financials(symbol, now)
                quote_session = aware(price_at).astimezone(NY).date().isoformat() if price_at else ""
                previous_close = next((r["close"] for r in reversed(daily) if r["session"] < quote_session), None)
                payload = {
                    "schema_version": "us_stock_evidence.v1",
                    "symbol": symbol,
                    "currency": "USD",
                    "regularMarketPrice": price,
                    "current_price": price,
                    "price_as_of": price_at,
                    "previousClose": previous_close,
                    "regularMarketVolume": number(meta.get("regularMarketVolume")),
                    "exchangeName": meta.get("exchangeName"),
                    "volume": daily[-1]["volume"],
                    "averageVolume": sum(r["volume"] for r in daily[-20:])
                    / len(daily[-20:]),
                    "volume_as_of": daily[-1]["end_at"],
                    "fetched_at": now.isoformat(),
                    "candle_recovery": recovered,
                    "candles": {
                        interval: {
                            "count": len(rows),
                            "latest_start_at": rows[-1]["start_at"],
                            "latest_end_at": rows[-1]["end_at"],
                            "latest_volume": rows[-1]["volume"],
                        }
                        for interval, rows in frames.items()
                    },
                    "fundamentals": fundamentals,
                    "valuation": valuation(fundamentals, price, price_as_of=price_at),
                    "price_plan": price_plan(daily, price),
                    "source_url": f"https://finance.yahoo.com/quote/{symbol}/history/",
                }
                filtered = db.apply_collection_policy("stocks", symbol, payload)
                if filtered is None:
                    continue
                filtered["data_quality"] = evidence_quality(filtered)
                incomplete = (
                    "us_stock_evidence_incomplete:"
                    + ",".join(filtered["data_quality"]["missing"])
                    if not filtered["data_quality"]["ready"] else None
                )
                # 정상 시세·완료봉을 재무 API 장애 때문에 함께 버리지 않는다.
                if filtered["data_quality"]["market_ready"]:
                    snapshots.append({
                        "symbol": symbol, "payload": filtered,
                        "frames": frames, "error": incomplete,
                    })
                if not filtered["data_quality"]["ready"]:
                    raise ValueError(incomplete)
            except Exception as exc:
                errors.append(f"{symbol}:{type(exc).__name__}:{exc}")
            await _request_spacing(delay, jitter)
    # 전체 실패 상태와 종목별 부분/완료 상태를 분리하고 기존 이력은 보존한다.
    if errors:
        # 재무 미완성 종목은 partial, 전체 근거 확보 종목은 completed로 공개한다.
        # 부모 job은 실패 원인을 그대로 남기며 투자 준비 완료로 위장하지 않는다.
        for snapshot in snapshots:
            child_id = f"{job_id}-{snapshot['symbol']}"
            db.create_job(child_id, "stocks")
            db.publish_stock_snapshots(child_id, [snapshot], error=snapshot["error"])
        raise RuntimeError("us_stock_collection_failed:" + ";".join(errors))
    return db.publish_stock_snapshots(job_id, snapshots)


def query_evidence(symbols: list[str] | None = None):
    requested = db.resolve_collection_symbols("stocks", symbols)
    requested = configured_symbols() if requested is None else requested
    records = []
    for symbol in dict.fromkeys(s.strip().upper() for s in requested):
        rows = db.query_market_data("stocks", symbol, 1)
        payload = rows[0]["payload"] if rows else {"symbol": symbol}
        quality = evidence_quality(payload)
        plan = payload.get("price_plan") or {}
        records.append(
            {
                "symbol": symbol,
                "job_id": rows[0]["job_id"] if rows else None,
                "payload": payload,
                "data_quality": quality,
                "cost_verified_3r": quality["ready"]
                and plan.get("cost_verified") is True
                and plan.get("meets_net_3r") is True
                and plan.get("costs") == cost_model(),
                "execution_authorized": False,
            }
        )
    return records

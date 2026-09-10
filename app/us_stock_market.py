"""미국 정규장 완료봉과 자료 신선도 계약. 주문 기능은 포함하지 않는다."""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import math
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

NY = ZoneInfo("America/New_York")
SETTLEMENT_LAG = timedelta(minutes=5)


def number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError, OverflowError):
        return None


def aware(value):
    try:
        parsed = pd.Timestamp(value)
        if parsed.tzinfo is None or pd.isna(parsed):
            return None
        return parsed.to_pydatetime().astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


@lru_cache(maxsize=1)
def calendar():
    return xcals.get_calendar("XNYS")


def session_bounds(day):
    cal = calendar()
    label = pd.Timestamp(day)
    if not cal.is_session(label):
        return None
    return cal.session_open(label).to_pydatetime(), cal.session_close(
        label
    ).to_pydatetime()


def required_market_times(now):
    """휴장·조기 폐장·DST를 반영한 마지막 필수 일봉/정확히 60분인 봉."""
    cutoff = now.astimezone(timezone.utc) - SETTLEMENT_LAG
    day = cutoff.astimezone(NY).date()
    daily_end = hourly_end = None
    for offset in range(12):
        bounds = session_bounds(day - timedelta(days=offset))
        if not bounds:
            continue
        opening, close = bounds
        if daily_end is None and close <= cutoff:
            daily_end = close
        hours = int((min(close, cutoff) - opening).total_seconds() // 3600)
        if hourly_end is None and hours >= 1:
            hourly_end = opening + timedelta(hours=hours)
        if daily_end is not None and hourly_end is not None:
            return daily_end, hourly_end
    raise ValueError("us_market_calendar_evidence_missing")


def completed_candles(chart, interval, now):
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    result = {}
    for index, timestamp in enumerate(chart.get("timestamp") or []):
        start = datetime.fromtimestamp(timestamp, timezone.utc)
        bounds = session_bounds(start.astimezone(NY).date())
        if not bounds:
            continue
        opening, close = bounds
        if interval == "1d":
            start, end = opening, close
        else:
            end = start + timedelta(hours=1)
            # 마지막 30분짜리 축약 봉을 60분 완료봉으로 위장하지 않는다.
            if (
                start < opening
                or end > close
                or (start - opening).total_seconds() % 3600
            ):
                continue
        if end > now - SETTLEMENT_LAG:
            continue
        values = {
            field: number((quote.get(field) or [])[index])
            if index < len(quote.get(field) or [])
            else None
            for field in ("open", "high", "low", "close", "volume")
        }
        if any(v is None for v in values.values()):
            continue
        if (
            min(values[k] for k in ("open", "high", "low", "close")) <= 0
            or values["volume"] < 0
        ):
            continue
        if (
            not values["low"]
            <= min(values["open"], values["close"])
            <= max(values["open"], values["close"])
            <= values["high"]
        ):
            continue
        result[start] = {
            **values,
            "is_complete": True,
            "session": start.astimezone(NY).date().isoformat(),
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "interval": interval,
            "source": "yahoo_chart",
            "adjustment": "provider_ohlc_not_dividend_adjusted",
        }
    return [result[key] for key in sorted(result)]


def evidence_quality(payload, now=None):
    now = now or datetime.now(timezone.utc)
    missing = []
    required_daily, required_hourly = required_market_times(now)
    price = number(payload.get("regularMarketPrice"))
    quote_at = aware(payload.get("price_as_of"))
    bounds = session_bounds(now.astimezone(NY).date())
    quote_floor = required_daily
    if bounds and bounds[0] <= now <= bounds[1] + SETTLEMENT_LAG:
        quote_floor = max(required_daily, now - timedelta(minutes=20))
    if (
        price is None
        or price <= 0
        or quote_at is None
        or quote_at < quote_floor - timedelta(minutes=5)
        or quote_at > now + timedelta(seconds=5)
    ):
        missing.append("fresh_price")
    for interval, required in (("1d", required_daily), ("60m", required_hourly)):
        coverage = payload.get("candles", {}).get(interval, {})
        end = aware(coverage.get("latest_end_at"))
        if not end or end < required or end > now or coverage.get("count", 0) < 20:
            missing.append(f"completed_{interval}")
        if number(coverage.get("latest_volume")) is None:
            missing.append(f"volume_{interval}")
    financials = payload.get("fundamentals") or {}
    for key in ("fcf", "net_debt", "roic_pct"):
        if number(financials.get(key)) is None:
            missing.append(key)
    fetched = aware(financials.get("fetched_at"))
    if not fetched or not timedelta(0) <= now - fetched <= timedelta(hours=48):
        missing.append("fresh_fundamentals")
    period = financials.get("period_end")
    try:
        age = (now.date() - datetime.fromisoformat(period).date()).days
        if not 0 <= age <= 550:
            missing.append("financial_period")
    except (ValueError, TypeError):
        missing.append("financial_period")
    if number((payload.get("valuation") or {}).get("market_cap_estimate")) is None:
        missing.append("valuation")
    return {
        "ready": not missing,
        "missing": missing,
        "checked_at": now.isoformat(),
        "required_daily_end": required_daily.isoformat(),
        "required_60m_end": required_hourly.isoformat(),
    }

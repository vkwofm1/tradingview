import os
import json
import math
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(os.environ.get("DB_PATH", "data.db"))

_local = threading.local()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
    return _local.conn


def init_db() -> None:
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id          TEXT PRIMARY KEY,
            collector   TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TEXT NOT NULL,
            finished_at TEXT,
            result_count INTEGER DEFAULT 0,
            error       TEXT
        );
        CREATE TABLE IF NOT EXISTS market_data (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      TEXT NOT NULL,
            collector   TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            payload     TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );
        CREATE TABLE IF NOT EXISTS market_candles (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id       TEXT NOT NULL,
            collector    TEXT NOT NULL,
            symbol       TEXT NOT NULL,
            interval     TEXT NOT NULL,
            candle_time  TEXT NOT NULL,
            payload      TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(id),
            UNIQUE (collector, symbol, interval, candle_time)
        );
        CREATE INDEX IF NOT EXISTS idx_md_symbol ON market_data(symbol);
        CREATE INDEX IF NOT EXISTS idx_md_collector ON market_data(collector);
        CREATE INDEX IF NOT EXISTS idx_mc_symbol ON market_candles(symbol);
        CREATE INDEX IF NOT EXISTS idx_mc_collector ON market_candles(collector);
        CREATE INDEX IF NOT EXISTS idx_mc_time ON market_candles(candle_time);
    """)


def create_job(job_id: str, collector: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    _conn().execute(
        "INSERT INTO jobs (id, collector, status, created_at) VALUES (?, ?, 'running', ?)",
        (job_id, collector, now),
    )
    _conn().commit()
    return {"id": job_id, "collector": collector, "status": "running", "created_at": now}


def finish_job(job_id: str, count: int, error: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    status = "failed" if error else "completed"
    _conn().execute(
        "UPDATE jobs SET status=?, finished_at=?, result_count=?, error=? WHERE id=?",
        (status, now, count, error, job_id),
    )
    _conn().commit()


def get_job(job_id: str) -> dict | None:
    row = _conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(limit: int = 20) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def insert_market_data(job_id: str, collector: str, symbol: str, payload: Any) -> None:
    now = datetime.now(timezone.utc).isoformat()
    _conn().execute(
        "INSERT INTO market_data (job_id, collector, symbol, payload, collected_at) VALUES (?,?,?,?,?)",
        (job_id, collector, symbol, json.dumps(payload), now),
    )
    _conn().commit()


def insert_market_candle(
    job_id: str,
    collector: str,
    symbol: str,
    interval: str,
    candle_time: str,
    payload: Any,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    _conn().execute(
        """
        INSERT INTO market_candles (job_id, collector, symbol, interval, candle_time, payload, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(collector, symbol, interval, candle_time)
        DO UPDATE SET job_id=excluded.job_id, payload=excluded.payload, collected_at=excluded.collected_at
        """,
        (job_id, collector, symbol, interval, candle_time, json.dumps(payload), now),
    )
    _conn().commit()



def query_market_data(
    collector: str | None = None, symbol: str | None = None, limit: int = 50
) -> list[dict]:
    clauses, params = [], []
    if collector:
        clauses.append("collector=?")
        params.append(collector)
    if symbol:
        clauses.append("symbol=?")
        params.append(symbol.upper())
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = _conn().execute(
        f"SELECT * FROM market_data {where} ORDER BY collected_at DESC LIMIT ?", params
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        result.append(d)
    return result


def query_market_candles(
    collector: str | None = None,
    symbol: str | None = None,
    interval: str | None = None,
    limit: int = 60,
) -> list[dict]:
    clauses, params = [], []
    if collector:
        clauses.append("collector=?")
        params.append(collector)
    if symbol:
        clauses.append("symbol=?")
        params.append(symbol.upper())
    if interval:
        clauses.append("interval=?")
        params.append(interval)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = _conn().execute(
        f"SELECT * FROM market_candles {where} ORDER BY candle_time DESC LIMIT ?", params
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        result.append(d)
    return result


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_price(payload: dict[str, Any]) -> float | None:
    for key in ("trade_price", "close", "closing_price", "usd", "price"):
        if key in payload:
            price = _to_float(payload.get(key))
            if price is not None:
                return price
    return None


def _compute_max_drawdown_pct(prices: list[float]) -> float | None:
    if len(prices) < 2:
        return None
    peak = prices[0]
    worst = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak <= 0:
            continue
        drawdown = ((price / peak) - 1.0) * 100.0
        worst = min(worst, drawdown)
    return abs(worst)


def _compute_realized_volatility_pct(prices: list[float]) -> float | None:
    if len(prices) < 3:
        return None
    returns = []
    for prev, curr in zip(prices, prices[1:]):
        if prev <= 0 or curr <= 0:
            continue
        returns.append(math.log(curr / prev))
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(len(returns)) * 100.0


def get_risk_dashboard(
    stale_after_sec: int = 7200,
    lookback_minutes: int = 60,
    drawdown_alert_pct: float = 5.0,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    conn = _conn()

    job_rows = [dict(row) for row in conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC"
    ).fetchall()]
    market_rows = [dict(row) for row in conn.execute(
        """
        SELECT md.*
        FROM market_data md
        JOIN (
            SELECT collector, symbol, MAX(collected_at) AS latest_collected_at
            FROM market_data
            GROUP BY collector, symbol
        ) latest
          ON latest.collector = md.collector
         AND latest.symbol = md.symbol
         AND latest.latest_collected_at = md.collected_at
        ORDER BY md.collector, md.symbol
        """
    ).fetchall()]
    candle_rows = [dict(row) for row in conn.execute(
        "SELECT * FROM market_candles WHERE interval=? ORDER BY collector, symbol, candle_time DESC",
        ("1m",),
    ).fetchall()]

    collectors: dict[str, dict[str, Any]] = {}
    jobs_24h = 0
    successful_jobs_24h = 0
    window_start = now - timedelta(hours=24)

    for row in job_rows:
        created_at = _parse_timestamp(row.get("created_at"))
        collector = row["collector"]
        data = collectors.setdefault(
            collector,
            {
                "collector": collector,
                "latest_job_status": None,
                "last_job_at": None,
                "seconds_since_last_job": None,
                "last_error": None,
                "last_result_count": None,
                "jobs_24h": 0,
                "success_rate_24h": None,
                "_successes_24h": 0,
            },
        )

        if data["last_job_at"] is None:
            data["latest_job_status"] = row["status"]
            data["last_job_at"] = row["created_at"]
            data["seconds_since_last_job"] = (
                int((now - created_at).total_seconds()) if created_at else None
            )
            data["last_error"] = row.get("error")
            data["last_result_count"] = row.get("result_count")

        if created_at and created_at >= window_start:
            jobs_24h += 1
            data["jobs_24h"] += 1
            if row["status"] == "completed":
                successful_jobs_24h += 1
                data["_successes_24h"] += 1

    for data in collectors.values():
        if data["jobs_24h"]:
            data["success_rate_24h"] = round(
                data["_successes_24h"] / data["jobs_24h"], 4
            )
        latest_age = data["seconds_since_last_job"]
        status = data["latest_job_status"]
        if latest_age is None:
            health = "unknown"
        elif status == "failed":
            health = "failing"
        elif latest_age > stale_after_sec:
            health = "stale"
        else:
            health = "healthy"
        data["health"] = health
        del data["_successes_24h"]

    concentration = []
    total_market_cap = 0.0
    for row in market_rows:
        payload = json.loads(row["payload"])
        market_cap = _to_float(payload.get("usd_market_cap") or payload.get("market_cap"))
        price = _extract_price(payload)
        if market_cap is not None and market_cap > 0:
            concentration.append(
                {
                    "collector": row["collector"],
                    "symbol": row["symbol"],
                    "market_cap": market_cap,
                    "price": price,
                    "collected_at": row["collected_at"],
                }
            )
            total_market_cap += market_cap

    for item in concentration:
        item["weight_pct"] = round(item["market_cap"] / total_market_cap * 100.0, 2)
    concentration.sort(key=lambda item: item["weight_pct"], reverse=True)

    grouped_candles: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in candle_rows:
        key = (row["collector"], row["symbol"])
        bucket = grouped_candles.setdefault(key, [])
        if len(bucket) >= lookback_minutes:
            continue
        payload = json.loads(row["payload"])
        price = _extract_price(payload)
        if price is None:
            continue
        bucket.append(
            {
                "collector": row["collector"],
                "symbol": row["symbol"],
                "candle_time": row["candle_time"],
                "price": price,
            }
        )

    price_risk = []
    alerts = []
    for collector_data in collectors.values():
        if collector_data["health"] == "failing":
            alerts.append(
                {
                    "severity": "high",
                    "code": "collector_failed",
                    "collector": collector_data["collector"],
                    "message": f"{collector_data['collector']} 수집 작업이 최근 실행에서 실패했습니다.",
                }
            )
        elif collector_data["health"] == "stale":
            alerts.append(
                {
                    "severity": "medium",
                    "code": "collector_stale",
                    "collector": collector_data["collector"],
                    "message": f"{collector_data['collector']} 수집 데이터가 {stale_after_sec}초 이상 갱신되지 않았습니다.",
                }
            )

    for (collector, symbol), rows in grouped_candles.items():
        ordered = list(reversed(rows))
        prices = [row["price"] for row in ordered]
        current_price = prices[-1] if prices else None
        return_pct_1h = None
        if len(prices) >= 2 and prices[0] > 0:
            return_pct_1h = ((prices[-1] / prices[0]) - 1.0) * 100.0
        max_drawdown_pct = _compute_max_drawdown_pct(prices)
        realized_volatility_pct = _compute_realized_volatility_pct(prices)
        price_risk.append(
            {
                "collector": collector,
                "symbol": symbol,
                "current_price": current_price,
                "return_pct_1h": round(return_pct_1h, 4) if return_pct_1h is not None else None,
                "max_drawdown_pct_1h": round(max_drawdown_pct, 4) if max_drawdown_pct is not None else None,
                "realized_volatility_pct_1h": round(realized_volatility_pct, 4)
                if realized_volatility_pct is not None
                else None,
                "points": len(prices),
            }
        )
        if max_drawdown_pct is not None and max_drawdown_pct >= drawdown_alert_pct:
            alerts.append(
                {
                    "severity": "high",
                    "code": "drawdown_breach",
                    "collector": collector,
                    "symbol": symbol,
                    "message": f"{symbol} 1시간 최대 낙폭이 {drawdown_alert_pct}% 임계치를 초과했습니다.",
                }
            )

    price_risk.sort(
        key=lambda item: item["max_drawdown_pct_1h"] if item["max_drawdown_pct_1h"] is not None else -1,
        reverse=True,
    )

    healthy_collectors = sum(1 for item in collectors.values() if item["health"] == "healthy")
    failing_collectors = sum(1 for item in collectors.values() if item["health"] == "failing")
    stale_collectors = sum(1 for item in collectors.values() if item["health"] == "stale")

    return {
        "generated_at": now.isoformat(),
        "overview": {
            "collector_count": len(collectors),
            "healthy_collectors": healthy_collectors,
            "failing_collectors": failing_collectors,
            "stale_collectors": stale_collectors,
            "jobs_24h": jobs_24h,
            "success_rate_24h": round(successful_jobs_24h / jobs_24h, 4) if jobs_24h else None,
            "alert_count": len(alerts),
        },
        "collectors": sorted(collectors.values(), key=lambda item: item["collector"]),
        "concentration": concentration[:10],
        "price_risk": price_risk[:20],
        "integration_gaps": [
            "실시간 포지션 노출도 및 P&L은 체결/포지션 원장 연동이 필요합니다.",
            "Kelly Criterion, stop-loss 효과 분석은 전략 실행 로그와 주문 이벤트 저장이 필요합니다.",
            "수동 개입 추적과 emergency stop 상태는 운영 이벤트 저장소 연동이 필요합니다.",
            "배포/인프라 상태는 argo-deploy 및 backend 메트릭 소스 연동이 필요합니다.",
        ],
        "alerts": alerts,
    }


def get_job_failure_rates(
    *,
    lookback_hours: int = 24,
    failure_rate_threshold_pct: float = 10.0,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=lookback_hours)
    rows = [dict(row) for row in _conn().execute(
        "SELECT collector, status, created_at, error FROM jobs ORDER BY created_at DESC"
    ).fetchall()]

    stats: dict[str, dict[str, Any]] = {}
    for row in rows:
        created_at = _parse_timestamp(row.get("created_at"))
        if not created_at or created_at < window_start:
            continue
        collector = row["collector"]
        item = stats.setdefault(
            collector,
            {
                "collector": collector,
                "jobs_24h": 0,
                "failed_jobs_24h": 0,
                "last_error": None,
            },
        )
        item["jobs_24h"] += 1
        if row["status"] == "failed":
            item["failed_jobs_24h"] += 1
            if item["last_error"] is None:
                item["last_error"] = row.get("error")

    threshold_ratio = failure_rate_threshold_pct / 100.0
    result = []
    for item in stats.values():
        failure_rate = item["failed_jobs_24h"] / item["jobs_24h"] if item["jobs_24h"] else 0.0
        result.append(
            {
                **item,
                "failure_rate_pct": round(failure_rate * 100.0, 2),
                "success_rate_pct": round((1.0 - failure_rate) * 100.0, 2) if item["jobs_24h"] else None,
                "alert": failure_rate > threshold_ratio if item["jobs_24h"] else False,
            }
        )
    return sorted(result, key=lambda item: (-item["failure_rate_pct"], item["collector"]))

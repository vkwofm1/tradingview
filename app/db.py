"""SQLite persistence layer for collected market data and job tracking."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import os

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

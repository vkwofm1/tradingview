from datetime import datetime, timezone

import pytest

from app import adoption_metrics, db


class _Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=()):
        self.connection.statements.append((sql, params))
        if self.connection.fail:
            raise RuntimeError("database write failed")

    def fetchone(self):
        return {"ok": True}

    def executemany(self, sql, rows):
        for row in rows:
            self.execute(sql, row)

    def fetchall(self):
        return [{"ok": True}]

    def close(self):
        return None


class _Connection:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_postgres_schema_uses_timezone_aware_timestamps(monkeypatch):
    connection = _Connection()
    monkeypatch.setattr(db, "_get_postgres_conn", lambda: connection)

    db._init_postgres_db()

    schema = "\n".join(sql for sql, _params in connection.statements)
    assert "TIMESTAMPTZ" in schema
    assert "candle_time  TIMESTAMPTZ NOT NULL" in schema
    assert "collected_at TIMESTAMPTZ NOT NULL" in schema
    assert "candle_time  TIMESTAMP NOT NULL" not in schema
    assert "idx_mc_job_collector_interval" in schema
    assert "market_candles(job_id, collector, interval)" in schema
    assert "idx_md_collector_job_id" in schema
    assert "market_data(collector, job_id)" in schema
    assert "idx_md_collector_symbol_time" in schema
    assert "market_data(collector, symbol, collected_at)" in schema
    assert "idx_mc_collector_symbol_time" in schema
    assert "market_candles(collector, symbol, candle_time)" in schema
    assert "idx_jobs_collector_status_created" in schema
    assert "jobs(collector, status, created_at DESC)" in schema
    assert connection.commits == 1


def test_latest_completed_job_uses_index_without_sorting_history(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite3")
    db._init_sqlite_db()
    conn = db._get_sqlite_conn()
    sql = "SELECT id,finished_at FROM jobs WHERE collector=? AND status=? ORDER BY created_at DESC LIMIT 1"
    plan = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, ("upbit", "completed")))
    assert "idx_jobs_collector_status_created" in plan
    assert "TEMP B-TREE" not in plan


@pytest.mark.parametrize("error", [None, "fcf_missing"])
def test_stock_snapshot_publishes_candles_data_and_completion_in_one_commit(monkeypatch, error):
    connection = _Connection()
    monkeypatch.setattr(db, "DB_TYPE", "postgres")
    monkeypatch.setattr(db, "_get_postgres_conn", lambda: connection)
    count = db.publish_stock_snapshots("job", [{"symbol": "AAPL", "frames": {"1d": [{"start_at": "2026-09-10T13:30:00+00:00"}]}, "payload": {"symbol": "AAPL"}}], error=error)
    assert count == 1
    assert connection.commits == 1
    assert len(connection.statements) == 3
    assert all("?" not in sql for sql, _ in connection.statements)
    assert "UPDATE jobs" in connection.statements[-1][0]
    params = connection.statements[-1][1]
    assert params[0] == ("partial" if error else "completed")
    assert params[-2] == error


def test_stock_postgres_publish_rolls_back_on_error(monkeypatch):
    connection = _Connection(fail=True)
    monkeypatch.setattr(db, "DB_TYPE", "postgres")
    monkeypatch.setattr(db, "_get_postgres_conn", lambda: connection)
    try:
        db.publish_stock_snapshots("job", [{"symbol": "AAPL", "frames": {}, "payload": {}}])
    except RuntimeError:
        pass
    else:
        raise AssertionError("write failure must propagate")
    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_postgres_execute_rolls_back_failed_transaction(monkeypatch):
    connection = _Connection(fail=True)
    monkeypatch.setattr(db, "_get_postgres_conn", lambda: connection)

    try:
        db._execute_postgres("INSERT INTO jobs(id) VALUES(?)", ("job-1",))
    except RuntimeError as exc:
        assert "database write failed" in str(exc)
    else:
        raise AssertionError("database failures must propagate")

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_postgres_reads_close_the_transaction(monkeypatch):
    connection = _Connection()
    monkeypatch.setattr(db, "_get_postgres_conn", lambda: connection)

    assert db._execute_postgres("SELECT 1", fetch_one=True) == {"ok": True}
    assert db._execute_postgres("SELECT 1", fetch_all=True) == [{"ok": True}]

    assert connection.commits == 2
    assert connection.rollbacks == 0


def test_unknown_database_type_fails_closed(monkeypatch):
    monkeypatch.setattr(db, "DB_TYPE", "unknown")

    try:
        db.init_db()
    except RuntimeError as exc:
        assert "Unsupported DB_TYPE" in str(exc)
    else:
        raise AssertionError("unknown database types must not fall back to SQLite")


def test_unknown_database_type_cannot_reach_crud_fallback(monkeypatch):
    monkeypatch.setattr(db, "DB_TYPE", "unknown")
    monkeypatch.setattr(
        db,
        "_execute_sqlite",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("SQLite fallback must not run")
        ),
    )

    try:
        db.create_job("job-1", "test")
    except RuntimeError as exc:
        assert "Unsupported DB_TYPE" in str(exc)
    else:
        raise AssertionError("unknown database types must fail before CRUD dispatch")

    try:
        adoption_metrics.log_system_action("user-1", "test")
    except RuntimeError as exc:
        assert "Unsupported DB_TYPE" in str(exc)
    else:
        raise AssertionError("adoption metrics must use the validated DB dispatcher")


def test_postgres_candle_write_interprets_naive_history_as_kst(monkeypatch):
    captured = {}

    def execute(_sql, params, **_kwargs):
        captured["params"] = params

    monkeypatch.setattr(db, "DB_TYPE", "postgres")
    monkeypatch.setattr(db, "_execute_postgres", execute)

    db.insert_market_candle(
        "job-1", "upbit", "KRW-BTC", "1m", "2026-07-11T19:25:00", {"close": 1}
    )

    candle_time = captured["params"][4]
    assert isinstance(candle_time, datetime)
    assert candle_time == datetime(2026, 7, 11, 10, 25, tzinfo=timezone.utc)


def test_postgres_candle_timezone_is_collector_specific(monkeypatch):
    captured = {}

    def execute(_sql, params, **_kwargs):
        captured["params"] = params

    monkeypatch.setattr(db, "DB_TYPE", "postgres")
    monkeypatch.setattr(db, "_execute_postgres", execute)
    db.insert_market_candle(
        "job-1", "bithumb", "BTC", "1m", "2026-07-11T10:25:00", {"close": 1}
    )
    assert captured["params"][4] == datetime(2026, 7, 11, 10, 25, tzinfo=timezone.utc)

    try:
        db.insert_market_candle(
            "job-1", "unknown", "X", "1m", "2026-07-11T10:25:00", {"close": 1}
        )
    except ValueError as exc:
        assert "unknown timezone" in str(exc)
    else:
        raise AssertionError("unknown naive candle timezone must fail closed")

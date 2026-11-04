"""Bounded source-side RPC; executed inside the existing TradingView pod."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import contextmanager

TABLES = ("market_data", "market_candles")
GIB = 1024**3


class Psycopg2Connection:
    """Expose the small psycopg3 API subset used by this bounded worker."""

    def __init__(self, connection):
        self.connection = connection
        self.connection.autocommit = True

    def execute(self, query, params=None):
        cursor = self.connection.cursor()
        cursor.execute(query, params)
        return cursor

    @contextmanager
    def transaction(self):
        self.connection.autocommit = False
        try:
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self.connection.autocommit = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()


def connect_database(url: str):
    try:
        import psycopg
    except ImportError:
        import psycopg2

        return Psycopg2Connection(psycopg2.connect(url))
    return psycopg.connect(url, autocommit=True)


def validate_table(table: str) -> str:
    if table not in TABLES:
        raise ValueError("table outside archival scope")
    return table


def eligible_sql(table: str, alias: str = "t") -> str:
    validate_table(table)
    # A minimum lookback is protection, never an age-only deletion trigger.
    same = f"n.collector={alias}.collector AND n.symbol={alias}.symbol"
    if table == "market_candles":
        time, keep = "candle_time", 2000
        same += f" AND n.interval={alias}.interval"
        extra = f"AND {alias}.interval IN ('1m','3m','5m','15m','30m','60m','1h')"
    else:
        time, keep, extra = "collected_at", 20, ""
    return f"""{alias}.{time} < now() - interval '90 days'
      AND {alias}.collected_at < now() - interval '90 days' {extra}
      AND EXISTS (SELECT 1 FROM jobs j WHERE j.id={alias}.job_id
        AND j.status IN ('completed','failed') AND j.finished_at IS NOT NULL)
      AND EXISTS (SELECT 1 FROM {table} n WHERE {same}
        AND n.{time} >= {alias}.{time} AND n.id <> {alias}.id
        ORDER BY n.{time} DESC OFFSET {keep - 1} LIMIT 1)"""


def select_sql(table: str) -> str:
    validate_table(table)
    time = "candle_time" if table == "market_candles" else "collected_at"
    return (f"SELECT to_jsonb(t) FROM {table} t WHERE {eligible_sql(table)} "
            f"ORDER BY t.{time},t.id LIMIT %s")


def delete_sql(table: str) -> str:
    validate_table(table)
    # Whole-row equality protects concurrent corrections, not just ids/timestamps.
    return f"""DELETE FROM {table} t USING jsonb_array_elements(%s::jsonb) r
      WHERE t.id=(r->>'id')::integer AND to_jsonb(t)=r
      AND {eligible_sql(table)} RETURNING t.id"""


def identity(conn) -> dict:
    system_id, dbname = conn.execute(
        "SELECT system_identifier::text,current_database() FROM pg_control_system()"
    ).fetchone()
    columns = conn.execute("""SELECT table_name,column_name,udt_name,is_nullable
      FROM information_schema.columns WHERE table_schema='public'
      AND table_name IN ('jobs','market_data','market_candles')
      ORDER BY table_name,ordinal_position""").fetchall()
    return {"system_id": system_id, "database": dbname,
            "schema_sha256": hashlib.sha256(json.dumps(columns).encode()).hexdigest()}


def capacity(conn) -> dict:
    folder = conn.execute("SHOW data_directory").fetchone()[0]
    # The app container does not mount PGDATA; return database-side path/size metadata.
    # The host validates that this PVC is backed by the monitored WSL filesystem.
    return {"data_directory": folder, "table_bytes": {
        table: conn.execute("SELECT pg_total_relation_size(%s::regclass)", (table,)).fetchone()[0]
        for table in TABLES}}


def dispatch(conn, request: dict) -> dict:
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute("SET statement_timeout='45s'")
    conn.execute("SET lock_timeout='1s'")
    conn.execute("SET jit=off")
    conn.execute("SET temp_file_limit='256MB'")
    actual = identity(conn)
    if request["action"] != "inspect" and "expected_source" not in request:
        raise RuntimeError("source identity is required before selecting or mutating data")
    if request.get("expected_source", actual) != actual:
        raise RuntimeError("source identity or schema changed")
    action = request["action"]
    if action == "inspect":
        return {"source": actual, **capacity(conn)}
    table = validate_table(request["table"])
    if action == "prepare":
        limit = int(request.get("limit", 2000))
        if not 1 <= limit <= 2000:
            raise ValueError("invalid batch limit")
        with conn.transaction():
            after_id = int(request.get("after_id", 0))
            if after_id < 0:
                raise ValueError("invalid scan cursor")
            # Bound work before applying expensive retention checks. Primary-key keyset
            # pagination also advances past protected rows, without sorting old history.
            ids = [r[0] for r in conn.execute(
                f"SELECT id FROM {table} WHERE id > %s ORDER BY id LIMIT %s",
                (after_id, limit),
            )]
            rows = [r[0] for r in conn.execute(
                f"SELECT to_jsonb(t) FROM {table} t WHERE t.id=ANY(%s) "
                f"AND {eligible_sql(table)} ORDER BY t.id", (ids,),
            )] if ids else []
            jobs = [r[0] for r in conn.execute(
                "SELECT to_jsonb(j) FROM jobs j WHERE id=ANY(%s) ORDER BY id",
                (list({r["job_id"] for r in rows}),),
            )] if rows else []
        return {"source": actual, "table": table, "rows": rows, "jobs": jobs,
                "next_after_id": max(ids) if ids else 0, "scan_complete": not ids}
    if action == "delete":
        rows = request["rows"]
        if not rows or len(rows) > 2000 or len({r["id"] for r in rows}) != len(rows):
            raise ValueError("invalid deletion batch")
        with conn.transaction():
            if not conn.execute("SELECT pg_try_advisory_xact_lock(746938201)").fetchone()[0]:
                raise RuntimeError("another source archive transaction is running")
            deleted = [r[0] for r in conn.execute(delete_sql(table), (json.dumps(rows),))]
        return {"deleted_ids": deleted, "skipped_changed_or_protected": len(rows) - len(deleted)}
    if action == "reclaim":
        before = capacity(conn)["table_bytes"][table]
        if before > int(request["max_table_bytes"]):
            raise RuntimeError("table grew beyond reserved rewrite budget")
        # Identifier is allowlisted; no CASCADE, whole-table deletion or unbounded lock.
        conn.execute("SET statement_timeout='15min'")
        conn.execute("SET temp_file_limit='8GB'")
        conn.execute("SET max_parallel_maintenance_workers=0")
        conn.execute(f"VACUUM (FULL, ANALYZE) {table}")
        after = capacity(conn)["table_bytes"][table]
        return {"table_before_bytes": before, "table_after_bytes": after,
                "relation_reclaimed_bytes": max(0, before - after)}
    raise ValueError("unknown action")


def main() -> None:
    request = json.load(sys.stdin)
    try:
        with connect_database(os.environ["DATABASE_URL"]) as conn:
            print(json.dumps(dispatch(conn, request)), flush=True)
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__,
                          "sqlstate": getattr(exc, "sqlstate", getattr(exc, "pgcode", None))}),
              flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

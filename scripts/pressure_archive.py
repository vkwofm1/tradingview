"""Capacity-triggered, verified D-drive archival with bounded source reclamation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import uuid

import archive_to_windows as archive

GIB = 1024**3
STATE = Path("/home/vkwofm/.local/state/tradingview-archive")
SOURCE_WORKER = Path(__file__).with_name("pressure_archive_source.py")
CONTEXT = "k3d-dev-cluster"
TABLES = ("market_candles", "market_data")
FIELDS = {
    "market_candles": "id,job_id,collector,symbol,interval,candle_time,payload,collected_at",
    "market_data": "id,job_id,collector,symbol,payload,collected_at",
}
MAX_BATCHES = 25


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def disk_state(path: str | Path = "/") -> dict:
    usage = shutil.disk_usage(path)
    return {"total": usage.total, "free": usage.free,
            "used_ratio": usage.used / max(1, usage.used + usage.free)}


def pressured(state: dict, rewrite_reserve: int = 0) -> bool:
    return state["free"] <= max(60 * GIB, rewrite_reserve + 20 * GIB) or state["used_ratio"] >= 0.85


def recovered(state: dict, rewrite_reserve: int = 0) -> bool:
    return state["free"] >= max(80 * GIB, rewrite_reserve + 40 * GIB) and state["used_ratio"] <= 0.80


def source(request: dict) -> dict:
    result = subprocess.run([
        "kubectl", "--context", CONTEXT, "--request-timeout=50s", "exec", "-i", "-n", "default",
        "deployment/tradingview", "--", "python", "-c", SOURCE_WORKER.read_text(),
    ], input=canonical(request), capture_output=True, timeout=55)
    if result.returncode:
        # Do not expose environment values or payloads in exception traces.
        try:
            error = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            error = {}
        raise RuntimeError(f"source {request['action']} failed: {error.get('error_type', 'transport')} "
                           f"SQLSTATE={error.get('sqlstate')}; no further source cleanup")
    return json.loads(result.stdout)


def save_state(value: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    value = {**value, "updated_at": datetime.now(timezone.utc).isoformat()}
    target = STATE / "pressure-status.json"
    temporary = target.with_suffix(".tmp")
    with temporary.open("w") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(target)
    directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    archive.report("pressure_archive", **value)


def require_backing_filesystem(expected: dict) -> None:
    identity_query = ("SELECT json_build_object('system_id',system_identifier::text,"
                      "'database',current_database()) FROM pg_control_system()")
    result = subprocess.run([
        "kubectl", "--context", CONTEXT, "--request-timeout=15s", "exec", "-n", "default",
        "statefulset/tradingview-postgres", "--", "sh", "-c",
        'df -kP "$PGDATA" | tail -n 1; exec psql -X -At -v ON_ERROR_STOP=1 '
        '-U "$POSTGRES_USER" -d "$POSTGRES_DB" -c ' + shlex.quote(identity_query),
    ], capture_output=True, text=True, check=True, timeout=20)
    lines = result.stdout.splitlines()
    fields = lines[0].split() if lines else []
    root = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/"],
                          capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    if not fields or fields[0] != root:
        raise RuntimeError("PostgreSQL volume is not on the monitored WSL root device")
    actual = json.loads(lines[1])
    if actual != {key: expected[key] for key in ("system_id", "database")}:
        raise RuntimeError("app database does not match the monitored PostgreSQL instance")


def require_reserves(table_bytes: int) -> None:
    reserve = table_bytes * 2 + 20 * GIB
    if disk_state()["free"] < reserve or disk_state("/mnt/c")["free"] < reserve:
        raise RuntimeError("insufficient WSL/C temporary rewrite reserve; source preserved")
    archive.require_archive_volume()
    if disk_state(archive.ROOT)["free"] < 50 * GIB:
        raise RuntimeError("D free space below 50GiB reserve; source preserved")


def initialize_sink(expected: dict) -> None:
    archive.require_archive_volume()
    settings = json.loads(archive.psql("""SELECT json_build_object(
      'directory',current_setting('data_directory'), 'fsync',current_setting('fsync'),
      'sync',current_setting('synchronous_commit'))""", archive.DATABASE))
    if (settings["directory"].replace("\\", "/").rstrip("/").lower() != "d:/postgresql/data"
            or settings["fsync"] != "on" or settings["sync"] != "on"):
        raise RuntimeError("archive durability or D data directory verification failed")
    columns = json.loads(archive.psql("""SELECT json_agg(json_build_array(
      table_name,column_name,udt_name,is_nullable) ORDER BY table_name,ordinal_position)
      FROM information_schema.columns WHERE table_schema='public'
      AND table_name IN ('jobs','market_data','market_candles')""", archive.DATABASE))
    if hashlib.sha256(json.dumps(columns).encode()).hexdigest() != expected["schema_sha256"]:
        raise RuntimeError("source/archive typed schema mismatch; source preserved")
    archive.psql("""CREATE SCHEMA IF NOT EXISTS archive_meta;
      CREATE TABLE IF NOT EXISTS archive_meta.pressure_batches (
        batch_id text PRIMARY KEY, verified_at timestamptz NOT NULL DEFAULT now(),
        manifest jsonb NOT NULL, snapshot jsonb NOT NULL);
      REVOKE ALL ON archive_meta.pressure_batches FROM PUBLIC;
      GRANT USAGE ON SCHEMA archive_meta TO archive_reader;
      GRANT SELECT ON archive_meta.pressure_batches TO archive_reader;
      CREATE OR REPLACE VIEW archive_meta.migrated_jobs AS
        SELECT b.batch_id, b.verified_at, r.* FROM archive_meta.pressure_batches b,
        LATERAL jsonb_populate_recordset(NULL::public.jobs,b.snapshot->'jobs') r;
      GRANT SELECT ON archive_meta.migrated_jobs TO archive_reader;
    """, archive.DATABASE)
    # Typed, indexed cold rows support symbol/time queries without unpacking every snapshot.
    archive.psql("CREATE SCHEMA IF NOT EXISTS archive_cold; GRANT USAGE ON SCHEMA archive_cold TO archive_reader;",
                 archive.DATABASE)
    for table in TABLES:
        columns = ",".join("r." + archive.ident(c) for c in FIELDS[table].split(","))
        time = "candle_time" if table == "market_candles" else "collected_at"
        archive.psql(f"""CREATE TABLE IF NOT EXISTS archive_cold.{table} (
          batch_id text NOT NULL REFERENCES archive_meta.pressure_batches(batch_id),
          LIKE public.{table}, PRIMARY KEY(batch_id,id));
          CREATE INDEX IF NOT EXISTS cold_{table}_symbol_time ON archive_cold.{table}(collector,symbol,{time});
          GRANT SELECT ON archive_cold.{table} TO archive_reader;
          CREATE OR REPLACE VIEW archive_meta.migrated_{table} AS
            SELECT r.batch_id,b.verified_at,{columns} FROM archive_cold.{table} r
            JOIN archive_meta.pressure_batches b USING(batch_id);
          GRANT SELECT ON archive_meta.migrated_{table} TO archive_reader;
        """, archive.DATABASE)


def persist_and_verify(batch: dict) -> tuple[str, str]:
    batch_id = "pressure_" + uuid.uuid4().hex
    folder = archive.ROOT / "backups/pressure"
    folder.mkdir(exist_ok=True)
    if folder.is_symlink() or folder.resolve().parent != (archive.ROOT / "backups").resolve():
        raise RuntimeError("unexpected recovery folder")
    if (archive.ROOT / "backups").is_symlink():
        raise RuntimeError("recovery directory must remain on the verified D drive")
    data = canonical(batch)
    if len(data) > 32 * 1024**2:
        raise RuntimeError("batch exceeds 32MiB; source preserved")
    digest = hashlib.sha256(data).hexdigest()
    path = folder / (batch_id + ".json.gz")
    with path.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb") as output:
            output.write(data)
        raw.flush()
        os.fsync(raw.fileno())
    with gzip.open(path, "rb") as stored:
        if hashlib.sha256(stored.read()).hexdigest() != digest:
            raise RuntimeError("D recovery file verification failed")
    manifest = {"sha256": digest, "file": path.name, "source": batch["source"],
                "table": batch["table"], "rows": len(batch["rows"])}
    table = batch["table"]
    if table not in TABLES:
        raise ValueError("unexpected archive table")
    archive.psql("BEGIN; SET LOCAL TIME ZONE 'UTC'; "
                 "INSERT INTO archive_meta.pressure_batches(batch_id,manifest,snapshot) VALUES ("
                 + ",".join(archive.literal(v) for v in (batch_id, json.dumps(manifest), data.decode()))
                 + f"); INSERT INTO archive_cold.{table} SELECT b.batch_id,r.* "
                 f"FROM archive_meta.pressure_batches b, LATERAL jsonb_populate_recordset(NULL::public.{table},"
                 f"b.snapshot->'rows') r WHERE b.batch_id={archive.literal(batch_id)}; COMMIT;", archive.DATABASE)
    # New read-only connection verifies committed database contents, not the write buffer.
    readback = json.loads(archive.psql("SELECT snapshot FROM archive_meta.pressure_batches WHERE batch_id="
                                      + archive.literal(batch_id), archive.DATABASE, "archive_reader"))
    if canonical(readback) != data:
        raise RuntimeError("archive committed readback mismatch; source preserved")
    typed = json.loads(archive.psql("SET TIME ZONE 'UTC'; SELECT jsonb_agg(to_jsonb(r)-'batch_id' ORDER BY id) "
                                   f"FROM archive_cold.{table} r WHERE batch_id={archive.literal(batch_id)};",
                                   archive.DATABASE, "archive_reader"))
    if canonical(typed) != canonical(sorted(batch["rows"], key=lambda r: r["id"])):
        raise RuntimeError("indexed archive row verification failed; source preserved")
    return batch_id, digest


def run(*, execute: bool, retry: bool = False) -> int:
    current = disk_state()
    status_file = STATE / "pressure-status.json"
    previous = json.loads(status_file.read_text()) if status_file.exists() else {}
    if previous.get("requires_attention") and not retry:
        archive.report("requires_attention", reason=previous.get("reason"), disk=current)
        return 1
    # Cheap relation-size metadata prevents large future tables from exhausting rewrite headroom
    # before a fixed free-space threshold would ever trigger. No source rows or D are read here.
    inspect = source({"action": "inspect"})
    expected = inspect["source"]
    budgets = {t: int(size * 1.1) + GIB for t, size in inspect["table_bytes"].items()}
    rewrite_reserve = max(budgets.values()) * 2 + 20 * GIB
    thresholds = {"trigger_free_bytes": max(60 * GIB, rewrite_reserve + 20 * GIB),
                  "recover_free_bytes": max(80 * GIB, rewrite_reserve + 40 * GIB)}
    needs_work = pressured(current, rewrite_reserve) or (
        previous.get("pressure_active", False) and not recovered(current, rewrite_reserve)
    )
    if not needs_work:
        if execute:
            save_state({"state": "idle", "disk": current, "source_deleted_rows": 0, **thresholds})
        else:
            archive.report("check", pressure=False, disk=current, **thresholds)
        return 0
    if not execute:
        archive.report("check", pressure=True, disk=current, source_deleted_rows=0, **thresholds)
        return 0
    require_backing_filesystem(expected)
    require_reserves(max(budgets.values()))
    initialize_sink(expected)
    total_deleted = 0
    for table in TABLES:
        table_deleted = 0
        for _ in range(MAX_BATCHES):
            if recovered(disk_state(), rewrite_reserve):
                break
            require_reserves(budgets[table])
            batch = source({"action": "prepare", "expected_source": expected, "table": table})
            if not batch["rows"]:
                break
            batch_id, digest = persist_and_verify(batch)
            require_reserves(budgets[table])
            if recovered(disk_state(), rewrite_reserve):
                break
            # A durable intent is written before deletion: interrupted/ambiguous operations latch.
            save_state({"state": "deletion_pending", "requires_attention": True,
                        "reason": "interrupted batch must be reconciled before retry",
                        "batch_id": batch_id, "sha256": digest, "table": table})
            result = source({"action": "delete", "expected_source": expected,
                             "table": table, "rows": batch["rows"]})
            count = len(result["deleted_ids"])
            table_deleted += count
            total_deleted += count
            archive.report("batch_archived", batch_id=batch_id, **result)
            if result["skipped_changed_or_protected"]:
                raise RuntimeError("source rows changed or became protected; archived snapshot preserved")
        if table_deleted:
            require_reserves(budgets[table])
            before = disk_state()
            result = source({"action": "reclaim", "expected_source": expected,
                             "table": table, "max_table_bytes": budgets[table]})
            after = disk_state()
            archive.report("reclaimed", table=table, disk_before=before, disk_after=after, **result)
            if result["relation_reclaimed_bytes"] <= 0 or after["free"] <= before["free"]:
                raise RuntimeError("no measurable physical space reclaimed; further cleanup stopped")
            if "table_after_bytes" in result:
                budgets[table] = int(result["table_after_bytes"] * 1.1) + GIB
                rewrite_reserve = max(budgets.values()) * 2 + 20 * GIB
        if recovered(disk_state(), rewrite_reserve):
            save_state({"state": "recovered", "source_deleted_rows": total_deleted, "disk": disk_state()})
            return 0
    if total_deleted:
        # Resume bounded work next tick only when each rewrite actually returned physical space.
        save_state({"state": "partial", "pressure_active": True,
                    "source_deleted_rows": total_deleted, "disk": disk_state()})
        return 0
    # Never drain protected history just because other WSL data keeps the disk full.
    save_state({"state": "needs_attention", "requires_attention": True,
                "reason": "bounded cycle did not recover target space; review protected data/other disk use",
                "source_deleted_rows": total_deleted, "disk": disk_state()})
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--retry", action="store_true", help="Explicitly retry a reconciled failure")
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            archive.report("already_running")
            return 0
        try:
            return run(execute=args.run, retry=args.retry)
        except Exception as exc:
            previous_path = STATE / "pressure-status.json"
            previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
            uncertain = bool(previous.get("requires_attention"))
            save_state({**previous, "state": "failed" if uncertain else "retry_wait",
                        "requires_attention": uncertain, "reason": str(exc)})
            return 1


if __name__ == "__main__":
    sys.exit(main())

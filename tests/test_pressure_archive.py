import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pressure = importlib.import_module("pressure_archive")
worker = importlib.import_module("pressure_archive_source")


def space(free=180, ratio=0.53):
    return {"free": free * pressure.GIB, "total": 400 * pressure.GIB, "used_ratio": ratio}


@pytest.mark.parametrize("free,ratio,expected", [(61, .84, False), (60, .5, True), (100, .85, True)])
def test_pressure_is_capacity_only(free, ratio, expected):
    assert pressure.pressured(space(free, ratio)) is expected


@pytest.mark.parametrize("free,ratio,expected", [(80, .8, True), (79, .7, False), (81, .81, False)])
def test_recovery_hysteresis(free, ratio, expected):
    assert pressure.recovered(space(free, ratio)) is expected


@pytest.mark.parametrize("execute", [False, True])
def test_healthy_capacity_only_reads_size_metadata_never_data_or_d(monkeypatch, tmp_path, execute):
    monkeypatch.setattr(pressure, "STATE", tmp_path)
    monkeypatch.setattr(pressure, "disk_state", lambda *_: space())
    def inspect(request):
        assert request == {"action": "inspect"}
        return {"source": {}, "table_bytes": {t: pressure.GIB for t in pressure.TABLES}}
    monkeypatch.setattr(pressure, "source", inspect)
    monkeypatch.setattr(pressure, "initialize_sink", lambda *_: pytest.fail("must not contact D"))
    assert pressure.run(execute=execute) == 0
    assert (tmp_path / "pressure-status.json").exists() is execute


def test_dry_run_under_pressure_has_no_mutations(monkeypatch, tmp_path):
    monkeypatch.setattr(pressure, "STATE", tmp_path)
    monkeypatch.setattr(pressure, "disk_state", lambda *_: space(50, .9))
    def inspect(request):
        assert request == {"action": "inspect"}
        return {"source": {}, "table_bytes": {t: pressure.GIB for t in pressure.TABLES}}
    monkeypatch.setattr(pressure, "source", inspect)
    assert pressure.run(execute=False) == 0
    assert list(tmp_path.iterdir()) == []


def test_latched_interruption_does_not_drain_more_history(monkeypatch, tmp_path):
    (tmp_path / "pressure-status.json").write_text('{"requires_attention":true}')
    monkeypatch.setattr(pressure, "STATE", tmp_path)
    monkeypatch.setattr(pressure, "disk_state", lambda *_: space(50, .9))
    monkeypatch.setattr(pressure, "source", lambda *_: pytest.fail("must not resume automatically"))
    assert pressure.run(execute=True) == 1


@pytest.mark.parametrize("table", ["market_candles", "market_data"])
def test_selection_and_deletion_preserve_recent_and_latest_rows(table):
    selection, deletion = worker.select_sql(table), worker.delete_sql(table)
    for query in (selection, deletion):
        assert "interval '90 days'" in query
        assert "finished_at IS NOT NULL" in query
        assert "OFFSET 1999" in query if table == "market_candles" else "OFFSET 19" in query
    assert "to_jsonb(t)=r" in deletion
    assert "RETURNING t.id" in deletion
    assert "'1d'" not in selection


def test_worker_rejects_arbitrary_table():
    with pytest.raises(ValueError):
        worker.delete_sql("jobs; DROP DATABASE tradingview")


def test_large_table_triggers_before_rewrite_headroom_is_lost():
    assert not pressure.pressured(space(130, .6))
    assert pressure.pressured(space(130, .6), rewrite_reserve=120 * pressure.GIB)
    assert not pressure.recovered(space(150, .6), rewrite_reserve=120 * pressure.GIB)


def test_rewrite_reserve_checked_before_d(monkeypatch):
    monkeypatch.setattr(pressure, "disk_state", lambda *_: space(25, .95))
    monkeypatch.setattr(pressure.archive, "require_archive_volume", lambda: pytest.fail("already unsafe"))
    with pytest.raises(RuntimeError, match="rewrite reserve"):
        pressure.require_reserves(10 * pressure.GIB)


def test_d_missing_prevents_cleanup(monkeypatch):
    monkeypatch.setattr(pressure, "disk_state", lambda *_: space())
    def missing():
        raise RuntimeError("D missing")
    monkeypatch.setattr(pressure.archive, "require_archive_volume", missing)
    with pytest.raises(RuntimeError, match="D missing"):
        pressure.require_reserves(pressure.GIB)


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    events = []
    current = space(55, .87)
    calls = 0
    batch = {"source": {"database": "test"}, "table": "market_candles",
             "rows": [{"id": 1}], "jobs": []}
    monkeypatch.setattr(pressure, "STATE", tmp_path)
    monkeypatch.setattr(pressure, "disk_state", lambda *_: dict(current))
    monkeypatch.setattr(pressure, "require_backing_filesystem", lambda *_: None)
    monkeypatch.setattr(pressure, "require_reserves", lambda *_: None)
    monkeypatch.setattr(pressure, "initialize_sink", lambda *_: events.append("sink_checked"))
    def persist(_):
        events.append("durable_file_and_db_readback_verified")
        return "pressure_test", "a" * 64
    monkeypatch.setattr(pressure, "persist_and_verify", persist)
    def source(request):
        nonlocal calls
        action = request["action"]
        events.append(action)
        if action == "inspect":
            return {"source": batch["source"], "table_bytes": {t: pressure.GIB for t in pressure.TABLES}}
        if action == "prepare":
            calls += 1
            return batch if calls == 1 else {**batch, "rows": []}
        if action == "delete":
            state = json.loads((tmp_path / "pressure-status.json").read_text())
            assert state["requires_attention"] and state["state"] == "deletion_pending"
            assert "durable_file_and_db_readback_verified" in events
            return {"deleted_ids": [1], "skipped_changed_or_protected": 0}
        if action == "reclaim":
            current.update(space(90, .75))
            return {"relation_reclaimed_bytes": pressure.GIB}
        raise AssertionError(action)
    monkeypatch.setattr(pressure, "source", source)
    return events, source


def test_end_to_end_order_and_physical_recovery(pipeline, tmp_path):
    events, _ = pipeline
    assert pressure.run(execute=True) == 0
    assert events.index("durable_file_and_db_readback_verified") < events.index("delete") < events.index("reclaim")
    state = json.loads((tmp_path / "pressure-status.json").read_text())
    assert state["state"] == "recovered" and state["source_deleted_rows"] == 1
    assert not state.get("requires_attention")


def test_failed_verification_never_deletes(pipeline, monkeypatch):
    events, _ = pipeline
    def mismatch(_):
        raise RuntimeError("archive committed readback mismatch")
    monkeypatch.setattr(pressure, "persist_and_verify", mismatch)
    with pytest.raises(RuntimeError, match="readback mismatch"):
        pressure.run(execute=True)
    assert "delete" not in events


def test_reclaim_timeout_latches_and_stops(pipeline, monkeypatch, tmp_path):
    events, original = pipeline
    def source(request):
        if request["action"] == "reclaim":
            raise RuntimeError("QueryCanceled SQLSTATE=57014")
        return original(request)
    monkeypatch.setattr(pressure, "source", source)
    with pytest.raises(RuntimeError, match="57014"):
        pressure.run(execute=True)
    assert json.loads((tmp_path / "pressure-status.json").read_text())["requires_attention"]
    assert events.count("delete") == 1
    assert pressure.run(execute=True) == 1


def test_source_correction_stops_further_cleanup(pipeline, monkeypatch):
    events, original = pipeline
    def source(request):
        if request["action"] == "delete":
            return {"deleted_ids": [], "skipped_changed_or_protected": 1}
        return original(request)
    monkeypatch.setattr(pressure, "source", source)
    with pytest.raises(RuntimeError, match="changed or became protected"):
        pressure.run(execute=True)
    assert "reclaim" not in events


def test_verified_partial_progress_continues_on_next_tick(pipeline, monkeypatch, tmp_path):
    _, original = pipeline
    free = 55
    monkeypatch.setattr(pressure, "disk_state", lambda *_: space(free, .87))
    def source(request):
        nonlocal free
        if request["action"] == "reclaim":
            free += 1
            return {"relation_reclaimed_bytes": pressure.GIB}
        return original(request)
    monkeypatch.setattr(pressure, "source", source)
    assert pressure.run(execute=True) == 0
    state = json.loads((tmp_path / "pressure-status.json").read_text())
    assert state["state"] == "partial" and state["pressure_active"]
    assert not state.get("requires_attention")


def test_no_eligible_history_stops_instead_of_relaxing_protection(pipeline, monkeypatch, tmp_path):
    events, original = pipeline
    def source(request):
        if request["action"] == "prepare":
            return {"rows": []}
        return original(request)
    monkeypatch.setattr(pressure, "source", source)
    assert pressure.run(execute=True) == 1
    assert "delete" not in events and "reclaim" not in events
    assert json.loads((tmp_path / "pressure-status.json").read_text())["requires_attention"]


def test_transient_failure_before_deletion_can_retry_next_tick(pipeline, monkeypatch, tmp_path):
    def unavailable(_):
        raise RuntimeError("D disconnected before deletion")
    monkeypatch.setattr(pressure, "initialize_sink", unavailable)
    monkeypatch.setattr(sys, "argv", ["pressure_archive.py", "--run"])
    assert pressure.main() == 1
    state = json.loads((tmp_path / "pressure-status.json").read_text())
    assert state["state"] == "retry_wait" and not state["requires_attention"]


def test_manifest_file_and_committed_readback(monkeypatch, tmp_path):
    (tmp_path / "backups").mkdir()
    monkeypatch.setattr(pressure.archive, "ROOT", tmp_path)
    batch = {"source": {"system_id": "1"}, "table": "market_data",
             "rows": [{"id": 1, "payload": "한글 ' \\"}], "jobs": []}
    calls = []
    def psql(query, database, user="archive_owner"):
        calls.append((query, user))
        if "jsonb_agg(to_jsonb(r)" in query:
            return json.dumps(batch["rows"])
        return json.dumps(batch) if user == "archive_reader" else ""
    monkeypatch.setattr(pressure.archive, "psql", psql)
    batch_id, digest = pressure.persist_and_verify(batch)
    with gzip.open(tmp_path / "backups/pressure" / (batch_id + ".json.gz"), "rb") as data:
        assert hashlib.sha256(data.read()).hexdigest() == digest
    assert "INSERT INTO" in calls[0][0] and calls[1][1] == "archive_reader"


@pytest.mark.skipif(os.environ.get("WINDOWS_ARCHIVE_INTEGRATION") != "1", reason="Windows DB opt-in")
def test_native_selection_corrections_archive_readback_and_reclaim(monkeypatch):
    """Isolated database only; no production source rows are modified."""
    database = "pressure_verify_" + uuid.uuid4().hex[:8]
    archive = pressure.archive
    archive.psql(f'CREATE DATABASE "{database}" TEMPLATE template0;')
    owned_files = []
    try:
        monkeypatch.setattr(archive, "DATABASE", database)
        native_psql = archive.psql
        def isolated_psql(query, database="postgres", user="archive_owner"):
            # Production HBA deliberately limits reader logins to tradingview_archive.
            # Keep it intact: use the identical read-only role inside this isolated test DB.
            if user == "archive_reader":
                query = "SET ROLE archive_reader; SET default_transaction_read_only=on;\n" + query
                user = "archive_owner"
            return native_psql(query, database, user)
        monkeypatch.setattr(archive, "psql", isolated_psql)
        archive.psql("""CREATE TABLE jobs(id text PRIMARY KEY,collector text,status text,
          created_at timestamptz,finished_at timestamptz,result_count integer,error text);
          CREATE TABLE market_data(id integer PRIMARY KEY,job_id text REFERENCES jobs,
          collector text,symbol text,payload text,collected_at timestamptz);
          CREATE TABLE market_candles(id integer PRIMARY KEY,job_id text REFERENCES jobs,
          collector text,symbol text,interval text,candle_time timestamptz,payload text,collected_at timestamptz);
          INSERT INTO jobs VALUES('j','test','completed',now()-interval '200 days',now()-interval '199 days',0,NULL);
          INSERT INTO market_data SELECT i,'j','test','OLD', '한국어 quoted '' '||i,
          now()-interval '200 days'+i*interval '1 minute' FROM generate_series(1,25)i;
          INSERT INTO market_candles SELECT i,'j','test','OLD','1m',
          now()-interval '200 days'+i*interval '1 minute','{}',now()-interval '100 days'
          FROM generate_series(1,2005)i;
          INSERT INTO market_candles VALUES(3000,'j','test','DAILY','1d',now()-interval '999 days','{}',now()-interval '998 days');
          GRANT CONNECT ON DATABASE """ + database + " TO archive_reader;", database)
        columns = json.loads(archive.psql("""SELECT json_agg(json_build_array(
          table_name,column_name,udt_name,is_nullable) ORDER BY table_name,ordinal_position)
          FROM information_schema.columns WHERE table_schema='public'
          AND table_name IN ('jobs','market_data','market_candles')""", database))
        expected = {"schema_sha256": hashlib.sha256(json.dumps(columns).encode()).hexdigest()}
        pressure.initialize_sink(expected)
        for table, remaining in (("market_data", 21), ("market_candles", 2002)):
            select = worker.select_sql(table).replace("%s", "20")
            rows = json.loads(archive.psql(f"SELECT json_agg(q.to_jsonb) FROM ({select}) q;", database))
            assert len(rows) == 5
            jobs = json.loads(archive.psql("SELECT json_agg(to_jsonb(j)) FROM jobs j;", database))
            batch = {"source": expected, "table": table, "rows": rows, "jobs": jobs}
            batch_id, _ = pressure.persist_and_verify(batch)
            owned_files.append(archive.ROOT / "backups/pressure" / (batch_id + ".json.gz"))
            assert int(archive.psql(f"SELECT count(*) FROM archive_meta.migrated_{table} WHERE batch_id='{batch_id}';",
                                    database, "archive_reader")) == 5
            # A source correction after archive verification must never be deleted.
            archive.psql(f"UPDATE {table} SET payload='corrected' WHERE id=1;", database)
            query = worker.delete_sql(table).replace("%s", archive.literal(json.dumps(rows)))
            assert len(archive.psql(query, database).splitlines()) == 4
            assert int(archive.psql(f"SELECT count(*) FROM {table};", database)) == remaining
            assert archive.psql(f"SELECT payload FROM {table} WHERE id=1;", database) == "corrected"
        # Verify actual physical shrinking, not only logical deletion/row counts.
        archive.psql("INSERT INTO market_data SELECT i,'j','test','BLOAT',repeat(md5(i::text),40),now() "
                     "FROM generate_series(10000,18000)i; DELETE FROM market_data WHERE id>=10000;", database)
        before = int(archive.psql("SELECT pg_total_relation_size('market_data');", database))
        archive.psql("SET lock_timeout='1s'; SET statement_timeout='30s'; VACUUM (FULL, ANALYZE) market_data;", database)
        after = int(archive.psql("SELECT pg_total_relation_size('market_data');", database))
        assert before > after
    finally:
        archive.psql(f'DROP DATABASE "{database}";')
        for path in owned_files:
            path.unlink()

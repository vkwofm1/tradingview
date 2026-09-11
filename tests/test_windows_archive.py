import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import pytest

spec = importlib.util.spec_from_file_location(
    'archive_to_windows', Path(__file__).resolve().parents[1] / 'scripts/archive_to_windows.py'
)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


@pytest.mark.parametrize('free,size,allowed', [
    (49, 1, False), (50, 1, True), (75, 22, False), (76, 22, True),
])
def test_space_guard_leaves_dump_restore_wal_reserve(free, size, allowed):
    assert archive.capacity_ok(free * archive.GIB, size * archive.GIB) is allowed


@pytest.mark.parametrize('name', ['x;DROP DATABASE postgres', '../x', 'x"y', '', '0name'])
def test_sql_identifier_guard(name):
    with pytest.raises(ValueError):
        archive.ident(name)


def test_merge_preserves_old_rows_and_checks_all_current_rows():
    info = {'schema': {t: [['id', 'int4', 'NO'], ['payload', 'text', 'NO']] for t in archive.TABLES},
            'counts': {t: 5 for t in archive.TABLES}}
    query = archive.merge_sql('archive_stage_test', info)
    assert query.startswith('BEGIN;') and query.endswith('COMMIT;')
    assert 'DELETE FROM' not in query and 'TRUNCATE' not in query
    assert query.count('ON CONFLICT (id) DO UPDATE') == 3
    assert query.count('IS NOT DISTINCT FROM ROW') == 3
    assert query.count('IS DISTINCT FROM ROW') == 3
    assert query.count('RAISE EXCEPTION') == 3
    assert 'DROP SERVER archive_incoming_server CASCADE' in query


def test_missing_id_is_fail_closed():
    info = {'schema': {t: [['payload', 'text', 'NO']] for t in archive.TABLES},
            'counts': {t: 5 for t in archive.TABLES}}
    with pytest.raises(ValueError):
        archive.merge_sql('archive_stage_test', info)


def test_restore_count_mismatch_is_not_verified(monkeypatch):
    monkeypatch.setattr(archive, 'psql', lambda *_: '9')
    with pytest.raises(RuntimeError, match='9 != 10'):
        archive.verify_counts({'counts': {'jobs': 10}}, 'stage')


def test_rotation_only_removes_owned_verified_pairs_and_keeps_two(tmp_path):
    for i in range(4):
        stem = f'snapshot_2026090{i + 1}T000000Z_abcdef12'
        (tmp_path / (stem + '.dump')).write_bytes(b'backup')
        (tmp_path / (stem + '.json')).write_text(json.dumps({'status': 'verified'}))
    other = tmp_path / 'snapshot_other.dump'
    other.write_bytes(b'keep')
    pending = tmp_path / 'snapshot_20260905T000000Z_abcdef12.dump'
    pending.write_bytes(b'not verified')
    partial = tmp_path / 'snapshot_20260906T000000Z_abcdef12.partial'
    partial.write_bytes(b'incomplete')
    removed = archive.rotate_verified_dumps(tmp_path, keep=1)
    assert len(removed) == 2
    assert other.exists() and pending.exists() and partial.exists()
    assert len(list(tmp_path.glob('*.json'))) == 2


def test_rotation_retains_both_source_and_cumulative_backups(tmp_path):
    stems = [f'snapshot_2026090{i + 1}T000000Z_abcdef12' for i in range(3)]
    for stem in stems:
        (tmp_path / (stem + '.dump')).write_bytes(b'cumulative')
        (tmp_path / (stem + '.source.dump')).write_bytes(b'source')
        (tmp_path / (stem + '.json')).write_text(json.dumps({
            'status': 'verified', 'source_dump': stem + '.source.dump',
        }))
    removed = archive.rotate_verified_dumps(tmp_path, keep=2)
    assert sorted(removed) == [stems[0] + '.dump', stems[0] + '.source.dump']
    for stem in stems[1:]:
        assert (tmp_path / (stem + '.dump')).exists()
        assert (tmp_path / (stem + '.source.dump')).exists()


def test_missing_d_drive_never_falls_back_into_wsl(monkeypatch):
    monkeypatch.setattr(archive.os.path, 'ismount', lambda _: False)
    with pytest.raises(RuntimeError, match='fallback'):
        archive.require_archive_volume()


def test_windows_tools_receive_native_argv0(monkeypatch):
    calls = []
    monkeypatch.setattr(archive.subprocess, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    archive.windows_run('pg_restore.exe', ['--list', 'D:/example.dump'], check=True)
    args, options = calls[0]
    assert args[0][0] == 'D:/PostgreSQL/16.15/pgsql/bin/pg_restore.exe'
    assert options['executable'] == str(archive.BIN / 'pg_restore.exe')


def test_archive_restore_keeps_all_data_constraints_and_lookup_indexes():
    toc = '\n'.join([
        '1; 0 1 TABLE DATA public market_candles owner',
        '2; 1 2 CONSTRAINT public market_candles market_candles_pkey owner',
        '3; 1 3 INDEX public idx_mc_collector owner',
        '4; 1 4 INDEX public idx_mc_collector_symbol_time owner',
        '5; 1 5 FK CONSTRAINT public market_candles market_candles_job_id_fkey owner',
        '6; 1 6 INDEX public newly_added_index owner',
    ])
    lines = archive.archive_restore_list(toc).splitlines()
    assert lines[2].startswith('; ')
    assert all(not lines[i].startswith(';') for i in (0, 1, 3, 4, 5))


def test_restore_pauses_staging_maintenance_before_loading_data(monkeypatch):
    events = []
    monkeypatch.setattr(archive, 'psql', lambda query, database: events.append(('sql', query, database)))
    monkeypatch.setattr(archive, 'windows_run', lambda tool, args, **kw: events.append(
        ('restore', args[args.index('--section') + 1], args[args.index('-j') + 1])))
    archive.restore_to_stage(archive.ROOT / 'backup.dump', 'stage_test', archive.ROOT / 'backup.list')
    assert events[0] == ('restore', 'pre-data', '1')
    assert all(event[0] == 'sql' and 'autovacuum_enabled=false' in event[1] and event[2] == 'stage_test'
               for event in events[1:4])
    assert events[4:] == [('restore', 'data', '1'), ('restore', 'post-data', '1')]


def test_dump_failure_keeps_partial_without_publishing(tmp_path):
    destination = tmp_path / 'backup.dump'
    with pytest.raises(RuntimeError, match='partial dump preserved'):
        archive.stream_dump([sys.executable, '-c', "print('partial'); raise SystemExit(1)"], destination)
    assert not destination.exists()
    assert destination.with_suffix('.partial').read_text() == 'partial\n'


@pytest.mark.skipif(os.environ.get('WINDOWS_ARCHIVE_INTEGRATION') != '1', reason='Windows DB opt-in')
def test_native_cumulative_dump_restores_historical_rows(monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    source, restored = 'archive_verify_dump_' + suffix, 'archive_verify_restore_' + suffix
    for database in (source, restored):
        archive.psql(f'CREATE DATABASE {archive.ident(database)} TEMPLATE template0;')
    try:
        for table in archive.TABLES:
            archive.psql(f'CREATE TABLE {archive.ident(table)}(id integer PRIMARY KEY,payload text);'
                         f"INSERT INTO {archive.ident(table)} VALUES(1,'historical'),(2,'current');", source)
        monkeypatch.setattr(archive, 'DATABASE', source)
        with tempfile.TemporaryDirectory(prefix='archive-verify-', dir=archive.ROOT / 'backups') as folder:
            dump = Path(folder) / 'archive.dump'
            digest, size = archive.dump_archive(dump)
            assert len(digest) == 64 and size > 100
            result = archive.windows_run('pg_restore.exe', ['--list', archive.winpath(dump)],
                                         check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            restore_list = Path(folder) / 'archive.list'
            restore_list.write_text(archive.archive_restore_list(result.stdout.decode()))
            archive.restore_to_stage(dump, restored, restore_list)
            assert archive.psql("SELECT string_agg(payload,',' ORDER BY id) FROM jobs;", restored) == 'historical,current'
    finally:
        for database in (source, restored):
            archive.psql(f'DROP DATABASE {archive.ident(database)};')


@pytest.mark.skipif(os.environ.get('WINDOWS_ARCHIVE_INTEGRATION') != '1', reason='Windows DB opt-in')
def test_native_merge_rolls_back_bad_counts_and_retains_old_rows():
    suffix = uuid.uuid4().hex[:8]
    stage, sink = 'archive_verify_stage_' + suffix, 'archive_verify_sink_' + suffix
    for database in (stage, sink):
        archive.psql(f'CREATE DATABASE {archive.ident(database)} TEMPLATE template0;')
    try:
        for database in (stage, sink):
            for table in archive.TABLES:
                archive.psql(f'CREATE TABLE {archive.ident(table)} (id integer PRIMARY KEY,payload text);', database)
        for table in archive.TABLES:
            archive.psql(f"INSERT INTO {archive.ident(table)} VALUES(1,'historical'),(2,'before');", sink)
            archive.psql(f"INSERT INTO {archive.ident(table)} VALUES(2,'after'),(3,'new');", stage)
        info = {'schema': {t: [['id', 'int4', 'NO'], ['payload', 'text', 'YES']] for t in archive.TABLES},
                'counts': {t: 2 for t in archive.TABLES}}
        bad = {**info, 'counts': {t: 99 for t in archive.TABLES}}
        with pytest.raises(RuntimeError, match='verification failed'):
            archive.psql(archive.merge_sql(stage, bad), sink)
        assert archive.psql('SELECT payload FROM jobs WHERE id=2;', sink) == 'before'
        archive.psql(archive.merge_sql(stage, info), sink)
        for table in archive.TABLES:
            assert archive.psql(
                f"SELECT string_agg(payload,',' ORDER BY id) FROM {archive.ident(table)};", sink,
            ) == 'historical,after,new'
        xmin = archive.psql('SELECT xmin FROM jobs WHERE id=2;', sink)
        archive.psql(archive.merge_sql(stage, info), sink)
        assert archive.psql('SELECT xmin FROM jobs WHERE id=2;', sink) == xmin
    finally:
        for database in (stage, sink):
            archive.psql(f'DROP DATABASE {archive.ident(database)};')

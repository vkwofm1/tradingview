"""WSL 원본은 읽기만 하고 D드라이브에 덤프와 누적 조회 DB를 보존한다."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path('/mnt/d/PostgreSQL')
BIN = ROOT / '16.15/pgsql/bin'
DATABASE = 'tradingview_archive'
TABLES = ('jobs', 'market_data', 'market_candles')
# 보관 조회에는 복합 인덱스의 선두 컬럼을 사용한다. 원본 덤프의 정의는 그대로 보존한다.
OMITTED_ARCHIVE_INDEXES = frozenset({
    'idx_mc_collector', 'idx_mc_job_collector_interval', 'idx_mc_symbol', 'idx_mc_time',
    'idx_md_collector', 'idx_md_collector_job_id', 'idx_md_symbol',
})
GIB = 1024**3
SCHEMA_SQL = """
SELECT json_object_agg(t.table_name, t.cols) FROM (
 SELECT table_name, json_agg(json_build_array(column_name,udt_name,is_nullable)
 ORDER BY ordinal_position) AS cols
 FROM information_schema.columns WHERE table_schema='public'
 AND table_name IN ('jobs','market_data','market_candles') GROUP BY table_name
) t
"""

# 별도 프로세스에서 읽기 전용 snapshot을 유지한다. 덤프와 행 수의 시점을 맞춘다.
SOURCE_SNAPSHOT = '''
import json,os,sys,psycopg
from psycopg import sql
conn=psycopg.connect(os.environ['DATABASE_URL'])
try:
 conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
 conn.execute("SET LOCAL statement_timeout='5min'")
 snapshot=conn.execute('SELECT pg_export_snapshot()').fetchone()[0]
 size=conn.execute('SELECT pg_database_size(current_database())').fetchone()[0]
 schema=conn.execute(SCHEMA_SQL).fetchone()[0]
 counts={t:conn.execute(sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(t))).fetchone()[0] for t in TABLES}
 print(json.dumps(dict(snapshot=snapshot,size=size,schema=schema,counts=counts)),flush=True)
 sys.stdin.buffer.read(1)
finally:
 conn.rollback()
 conn.close()
'''


def ident(value: str) -> str:
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', value):
        raise ValueError('unsafe SQL identifier')
    return '"' + value + '"'


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def winpath(path: Path) -> str:
    relative = path.relative_to(ROOT)
    return 'D:/PostgreSQL/' + relative.as_posix()


def report(event: str, **values: object) -> None:
    print(json.dumps({'event': event, **values}, ensure_ascii=False), flush=True)


def windows_run(tool: str, arguments: list[str], **options):
    # argv[0]도 Windows 경로여야 pg_restore가 자신의 실행 파일을 찾는다.
    executable = BIN / tool
    return subprocess.run([winpath(executable), *arguments], executable=str(executable), **options)


def psql(query: str, database: str = 'postgres', user: str = 'archive_owner') -> str:
    result = windows_run(
        'psql.exe', ['-X', '-w', '-qAt', '-h', '127.0.0.1',
         '-p', '55432', '-U', user, '-d', database, '-v', 'ON_ERROR_STOP=1', '-f', '-'],
        input=('\\encoding UTF8\n' + query).encode(), capture_output=True, timeout=3600,
    )
    if result.returncode:
        raise RuntimeError('archive SQL failed: ' + result.stderr.decode('utf-8', 'replace')[-1500:])
    return result.stdout.decode('utf-8').strip()


def capacity_ok(free_bytes: int, source_bytes: int) -> bool:
    # 복원 staging, 백업 파일, WAL 여유를 확보하고 원본은 절대 정리하지 않는다.
    return free_bytes >= max(50 * GIB, source_bytes * 3 + 10 * GIB)


def require_archive_volume() -> None:
    if not os.path.ismount('/mnt/d') or os.stat('/mnt/d').st_dev == os.stat('/').st_dev:
        raise RuntimeError('D drive is not mounted; refusing WSL-local fallback')
    if ROOT.is_symlink() or not (ROOT / 'data/PG_VERSION').is_file():
        raise RuntimeError('initialized Windows archive database missing')


@contextmanager
def source_snapshot():
    code = 'SCHEMA_SQL=' + repr(SCHEMA_SQL) + '\nTABLES=' + repr(TABLES) + '\n' + SOURCE_SNAPSHOT
    process = subprocess.Popen(
        ['kubectl', 'exec', '-i', '-n', 'default', 'deployment/tradingview', '--', 'python', '-c', code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        info = json.loads(process.stdout.readline())
        if set(info['schema']) != set(TABLES) or set(info['counts']) != set(TABLES):
            raise RuntimeError('required market tables missing')
        yield info
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()


def dump_snapshot(snapshot: str, destination: Path) -> tuple[str, int]:
    if not re.fullmatch(r'[0-9A-Fa-f-]+', snapshot):
        raise ValueError('invalid exported snapshot')
    command = [
        'kubectl', 'exec', '-n', 'default', 'statefulset/tradingview-postgres', '--',
        'sh', '-c', 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
        '--format=custom --no-owner --no-acl --lock-wait-timeout=5000 --snapshot="$1"',
        'sh', snapshot,
    ]
    return stream_dump(command, destination)


def stream_dump(command: list[str], destination: Path, executable: str | None = None) -> tuple[str, int]:
    partial = destination.with_suffix('.partial')
    hasher = hashlib.sha256()
    size = 0
    with partial.open('xb') as output:
        process = subprocess.Popen(command, executable=executable, stdout=subprocess.PIPE)
        assert process.stdout is not None  # stdout=PIPE 계약을 정적 검사에서도 명확히 한다.
        try:
            while chunk := process.stdout.read(1024 * 1024):
                output.write(chunk)
                hasher.update(chunk)
                size += len(chunk)
            if process.wait(timeout=60) != 0:
                raise RuntimeError('pg_dump failed; partial dump preserved')
            output.flush()
            os.fsync(output.fileno())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            process.stdout.close()
    if size < 100:
        raise RuntimeError('empty dump')
    # 검증 전 백업으로 기존 파일을 덮어쓰지 않는다.
    if destination.exists():
        raise FileExistsError(destination)
    partial.rename(destination)
    return hasher.hexdigest(), size


def dump_archive(destination: Path) -> tuple[str, int]:
    executable = BIN / 'pg_dump.exe'
    return stream_dump([
        winpath(executable), '-h', '127.0.0.1', '-p', '55432', '-U', 'archive_owner',
        '-w', '-d', DATABASE, '--format=custom', '--no-owner', '--no-acl',
        '--lock-wait-timeout=5000',
    ], destination, executable=str(executable))


def archive_restore_list(toc: str) -> str:
    lines = []
    for line in toc.splitlines():
        match = re.match(r'^\d+; \d+ \d+ INDEX public (\S+) ', line)
        if match and match.group(1) in OMITTED_ARCHIVE_INDEXES:
            line = '; ' + line
        lines.append(line)
    return '\n'.join(lines) + '\n'


def restore_to_stage(source_dump: Path, stage: str, restore_list: Path) -> None:
    arguments = [
        '-h', '127.0.0.1', '-p', '55432', '-U', 'archive_owner',
        '-w', '-d', stage, '--exit-on-error', '--no-owner', '--no-acl', '-j', '1',
        '--use-list', winpath(restore_list),
    ]
    for section in ('pre-data', 'data', 'post-data'):
        if section == 'data':
            for table in TABLES:
                psql(f'ALTER TABLE public.{ident(table)} SET (autovacuum_enabled=false);', stage)
        windows_run('pg_restore.exe', [*arguments, '--section', section, winpath(source_dump)],
                    check=True, timeout=7200)


def verify_counts(info: dict, database: str) -> None:
    for table, expected in info['counts'].items():
        count = int(psql(f'SELECT count(*) FROM public.{ident(table)};', database))
        if count != expected:
            raise RuntimeError(f'{table}: restored row count {count} != {expected}')


def merge_sql(stage: str, info: dict) -> str:
    ident(stage)
    statements = [
        'BEGIN;', "SET LOCAL statement_timeout='30min';",
        'CREATE EXTENSION IF NOT EXISTS postgres_fdw;',
        'CREATE SCHEMA archive_incoming;',
        'CREATE SERVER archive_incoming_server FOREIGN DATA WRAPPER postgres_fdw '
        f"OPTIONS(host '127.0.0.1',port '55432',dbname {literal(stage)},fetch_size '10000');",
        "CREATE USER MAPPING FOR archive_owner SERVER archive_incoming_server OPTIONS(user 'archive_owner');",
        'IMPORT FOREIGN SCHEMA public LIMIT TO (jobs,market_data,market_candles) '
        'FROM SERVER archive_incoming_server INTO archive_incoming;',
    ]
    for table in TABLES:
        columns = [column[0] for column in info['schema'][table]]
        if 'id' not in columns:
            raise ValueError('missing primary key')
        cols = ','.join(ident(column) for column in columns)
        updates = ','.join(f'{ident(c)}=EXCLUDED.{ident(c)}' for c in columns if c != 'id')
        old = ','.join('archived.' + ident(c) for c in columns)
        new = ','.join('EXCLUDED.' + ident(c) for c in columns)
        incoming = ','.join('s.' + ident(c) for c in columns)
        stored = ','.join('a.' + ident(c) for c in columns)
        statements += [
            f'INSERT INTO public.{ident(table)} AS archived ({cols}) SELECT {cols} '
            f'FROM archive_incoming.{ident(table)} WHERE true '
            f'ON CONFLICT (id) DO UPDATE SET {updates} '
            f'WHERE ROW({old}) IS DISTINCT FROM ROW({new});',
            # 원본에서 사라진 과거 행은 삭제하지 않고 현재 원본과 겹치는 행만 대조한다.
            'DO $$ BEGIN IF (SELECT count(*) FROM '
            f'archive_incoming.{ident(table)} s JOIN public.{ident(table)} a USING(id) '
            f'WHERE ROW({incoming}) IS NOT DISTINCT FROM ROW({stored})) <> {int(info["counts"][table])} '
            f"THEN RAISE EXCEPTION 'archive verification failed: {table}'; END IF; END $$;",
        ]
    statements += ['DROP SERVER archive_incoming_server CASCADE;',
                   'DROP SCHEMA archive_incoming;', 'COMMIT;']
    return '\n'.join(statements)


def configure_reader() -> None:
    # 대용량 staging 적재 중 중지했던 자동 유지관리를 게시된 DB에서 다시 켠다.
    for table in TABLES:
        psql(f'ALTER TABLE public.{ident(table)} RESET (autovacuum_enabled);', DATABASE)
    psql("""DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='archive_reader')
      THEN CREATE ROLE archive_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
      END IF; END $$;
      ALTER ROLE archive_reader SET default_transaction_read_only=on;
      ALTER ROLE archive_reader SET statement_timeout='60s';
    """)
    psql("""
      REVOKE ALL ON DATABASE tradingview_archive FROM PUBLIC;
      GRANT CONNECT ON DATABASE tradingview_archive TO archive_reader;
      REVOKE CREATE ON SCHEMA public FROM PUBLIC;
      GRANT USAGE ON SCHEMA public TO archive_reader;
      GRANT SELECT ON ALL TABLES IN SCHEMA public TO archive_reader;
      ALTER DEFAULT PRIVILEGES FOR ROLE archive_owner IN SCHEMA public
        GRANT SELECT ON TABLES TO archive_reader;
      CREATE SCHEMA IF NOT EXISTS archive_meta;
      CREATE TABLE IF NOT EXISTS archive_meta.runs (
        run_id text PRIMARY KEY, completed_at timestamptz NOT NULL,
        dump_sha256 text NOT NULL, metadata jsonb NOT NULL
      );
      CREATE OR REPLACE VIEW archive_meta.candle_coverage AS
        SELECT collector,symbol,interval,count(*) AS rows,
          min(candle_time) AS oldest,max(candle_time) AS newest
        FROM public.market_candles GROUP BY collector,symbol,interval;
      GRANT USAGE ON SCHEMA archive_meta TO archive_reader;
      GRANT SELECT ON ALL TABLES IN SCHEMA archive_meta TO archive_reader;
    """, DATABASE)


def rotate_verified_dumps(backup_dir: Path, keep: int = 7) -> list[str]:
    verified = []
    for manifest in backup_dir.glob('snapshot_*.json'):
        if manifest.is_symlink():
            continue
        info = json.loads(manifest.read_text())
        if info.get('status') != 'verified' or not re.fullmatch(
            r'snapshot_\d{8}T\d{6}Z_[a-f0-9]{8}', manifest.stem
        ):
            continue
        dump = manifest.with_suffix('.dump')
        if dump.is_file() and not dump.is_symlink():
            verified.append((manifest, dump, info))
    removed = []
    for manifest, dump, info in sorted(verified, key=lambda entry: entry[0], reverse=True)[max(2, keep):]:
        # 이 작업이 만든 검증 완료 덤프만 정리한다. 보관 DB의 과거 행은 유지한다.
        source_dump = dump.with_suffix('.source.dump')
        if (info.get('source_dump') == source_dump.name
                and source_dump.is_file() and not source_dump.is_symlink()):
            source_dump.unlink()
            removed.append(source_dump.name)
        dump.unlink()
        manifest.unlink()
        removed.append(dump.name)
    return removed


def run() -> None:
    require_archive_volume()
    subprocess.run([
        '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe', '-NoProfile',
        '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
        'D:\\PostgreSQL\\windows_archive_server.ps1', '-Action', 'Start',
    ], check=True, timeout=90)
    run_id = datetime.now(timezone.utc).strftime('snapshot_%Y%m%dT%H%M%SZ_') + uuid.uuid4().hex[:8]
    backups = ROOT / 'backups'
    dump = backups / (run_id + '.dump')
    stage = 'archive_stage_' + run_id.lower()
    with source_snapshot() as info:
        free = shutil.disk_usage(ROOT).free
        if not capacity_ok(free, info['size']):
            raise RuntimeError('D free space below safety reserve; source untouched')
        existing = psql(f'SELECT 1 FROM pg_database WHERE datname={literal(DATABASE)};') == '1'
        if existing:
            if json.loads(psql(SCHEMA_SQL, DATABASE)) != info['schema']:
                raise RuntimeError('source schema changed; review required before merge')
        source_dump = backups / (run_id + '.source.dump') if existing else dump
        report('dump_started', run_id=run_id, source_bytes=info['size'], counts=info['counts'])
        digest, size = dump_snapshot(info['snapshot'], source_dump)
    report('dump_complete', bytes=size, sha256=digest)
    source_digest, source_size = digest, size
    # 복원 실패 시 이미 수집한 덤프와 동일 시점 검증 정보를 보존한다.
    pending = backups / (run_id + '.pending.json')
    with pending.open('x') as output:
        json.dump({'status': 'pending', 'run_id': run_id, 'source': info,
                   'source_dump': source_dump.name, 'sha256': digest, 'dump_bytes': size}, output)
    toc = windows_run('pg_restore.exe', ['--list', winpath(source_dump)],
                      capture_output=True, check=True, timeout=60)
    restore_list = backups / (run_id + '.restore.list')
    with restore_list.open('x') as output:
        output.write(archive_restore_list(toc.stdout.decode()))
    psql(f'CREATE DATABASE {ident(stage)} OWNER archive_owner TEMPLATE template0;')
    report('restore_started', database=stage)
    restore_to_stage(source_dump, stage, restore_list)
    verify_counts(info, stage)
    if psql(f'SELECT 1 FROM pg_database WHERE datname={literal(DATABASE)};') != '1':
        psql(f'ALTER DATABASE {ident(stage)} RENAME TO {ident(DATABASE)};')
    else:
        report('merge_started')
        psql(merge_sql(stage, info), DATABASE)
        # 검증 완료된 보관 DB가 있을 때만 이번 임시 복원 DB를 삭제한다.
        psql(f'DROP DATABASE {ident(stage)};')
    configure_reader()
    if existing:
        archive_bytes = int(psql('SELECT pg_database_size(current_database());', DATABASE))
        if shutil.disk_usage(ROOT).free < archive_bytes + 10 * GIB:
            raise RuntimeError('D free space insufficient for cumulative dump; source dump preserved')
        report('cumulative_dump_started')
        digest, size = dump_archive(dump)
        windows_run('pg_restore.exe', ['--list', winpath(dump)],
                    stdout=subprocess.DEVNULL, check=True, timeout=60)
    completed = datetime.now(timezone.utc).isoformat()
    manifest = {'status': 'verified', 'run_id': run_id, 'completed_at': completed,
                'sha256': digest, 'dump_bytes': size, 'source': info,
                'source_dump': source_dump.name, 'source_sha256': source_digest,
                'source_dump_bytes': source_size,
                'backup_scope': 'cumulative_archive' if existing else 'initial_source_snapshot',
                'source_restore_counts_verified': True,
                'source_deletion_enabled': False}
    psql('INSERT INTO archive_meta.runs VALUES (' + ','.join([
        literal(run_id), literal(completed), literal(digest), literal(json.dumps(manifest)),
    ]) + ');', DATABASE)
    with dump.with_suffix('.json').open('x') as output:
        json.dump(manifest, output, ensure_ascii=False, indent=2)
    pending.unlink()
    restore_list.unlink()
    removed = rotate_verified_dumps(backups)
    report('verified', run_id=run_id, database=DATABASE, removed_own_old_dumps=removed,
           source_deletion_enabled=False, d_free_bytes=shutil.disk_usage(ROOT).free)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='백업과 검증 후 누적 조회 DB 갱신')
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    state = Path('/home/vkwofm/.local/state/tradingview-archive')
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            report('already_running')
            return 0
        try:
            run()
        except Exception as exc:
            report('failed', error=str(exc), source_deletion_enabled=False)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

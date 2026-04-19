#!/usr/bin/env python3
"""
Migrate SQLite database to PostgreSQL.

Usage:
    python scripts/migrate_to_postgres.py [--source-db /path/to/data.db] [--target-url postgresql://...]
"""

import sqlite3
import json
import sys
import argparse
from datetime import datetime
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Migrate SQLite database to PostgreSQL")
    parser.add_argument(
        "--source-db",
        default="data.db",
        help="Path to SQLite database (default: data.db)",
    )
    parser.add_argument(
        "--target-url",
        default="postgresql://tradingview:tradingview_dev_password@localhost:5432/tradingview",
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without actually doing it",
    )
    return parser.parse_args()


def migrate_sqlite_to_postgres(sqlite_path: str, pg_url: str, dry_run: bool = False):
    """Migrate data from SQLite to PostgreSQL."""
    print(f"Starting migration from SQLite ({sqlite_path}) to PostgreSQL")
    print(f"Target URL: {pg_url}")
    if dry_run:
        print("[DRY RUN MODE]")
    print()

    # Connect to SQLite
    try:
        sqlite_conn = sqlite3.connect(sqlite_path)
        sqlite_conn.row_factory = sqlite3.Row
        cursor = sqlite_conn.cursor()
    except sqlite3.Error as e:
        print(f"❌ Failed to connect to SQLite: {e}")
        return False

    # Connect to PostgreSQL
    try:
        import psycopg
        pg_conn = psycopg.connect(pg_url)
    except Exception as e:
        print(f"❌ Failed to connect to PostgreSQL: {e}")
        sqlite_conn.close()
        return False

    pg_cursor = pg_conn.cursor()

    try:
        # Fetch row counts from SQLite
        tables = ["jobs", "market_data", "market_candles"]
        row_counts = {}

        for table in tables:
            cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            count = cursor.fetchone()["cnt"]
            row_counts[table] = count
            print(f"📊 SQLite {table}: {count} rows")

        print()

        if dry_run:
            print("[DRY RUN] Would migrate the following data:")
            print(f"  - jobs: {row_counts['jobs']} records")
            print(f"  - market_data: {row_counts['market_data']} records")
            print(f"  - market_candles: {row_counts['market_candles']} records")
            sqlite_conn.close()
            pg_conn.close()
            return True

        # Migrate jobs table
        print("📦 Migrating jobs table...")
        cursor.execute("SELECT * FROM jobs")
        jobs = cursor.fetchall()

        for job in jobs:
            pg_cursor.execute(
                """
                INSERT INTO jobs (id, collector, status, created_at, finished_at, result_count, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    job["id"],
                    job["collector"],
                    job["status"],
                    job["created_at"],
                    job["finished_at"],
                    job["result_count"],
                    job["error"],
                ),
            )

        pg_conn.commit()
        print(f"✅ Migrated {len(jobs)} jobs")

        # Migrate market_data table
        print("📦 Migrating market_data table...")
        cursor.execute("SELECT * FROM market_data")
        market_data = cursor.fetchall()

        for row in market_data:
            pg_cursor.execute(
                """
                INSERT INTO market_data (job_id, collector, symbol, payload, collected_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    row["job_id"],
                    row["collector"],
                    row["symbol"],
                    row["payload"],
                    row["collected_at"],
                ),
            )

        pg_conn.commit()
        print(f"✅ Migrated {len(market_data)} market_data records")

        # Migrate market_candles table
        print("📦 Migrating market_candles table...")
        cursor.execute("SELECT * FROM market_candles")
        candles = cursor.fetchall()

        for row in candles:
            pg_cursor.execute(
                """
                INSERT INTO market_candles
                (job_id, collector, symbol, interval, candle_time, payload, collected_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (collector, symbol, interval, candle_time) DO UPDATE
                SET job_id=excluded.job_id, payload=excluded.payload, collected_at=excluded.collected_at
                """,
                (
                    row["job_id"],
                    row["collector"],
                    row["symbol"],
                    row["interval"],
                    row["candle_time"],
                    row["payload"],
                    row["collected_at"],
                ),
            )

        pg_conn.commit()
        print(f"✅ Migrated {len(candles)} candle records")

        # Verify migration
        print()
        print("🔍 Verifying migration...")
        for table in tables:
            pg_cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            pg_count = pg_cursor.fetchone()[0]
            sqlite_count = row_counts[table]
            match = "✅" if pg_count == sqlite_count else "⚠️"
            print(f"{match} {table}: SQLite={sqlite_count}, PostgreSQL={pg_count}")

        print()
        print("✅ Migration completed successfully!")
        return True

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        pg_conn.rollback()
        return False

    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    args = parse_args()

    if not Path(args.source_db).exists():
        print(f"❌ SQLite database not found: {args.source_db}")
        sys.exit(1)

    success = migrate_sqlite_to_postgres(args.source_db, args.target_url, args.dry_run)
    sys.exit(0 if success else 1)

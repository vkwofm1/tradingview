#!/usr/bin/env python3
"""
Verify SQLite to PostgreSQL migration success.

Compares row counts, data samples, and schema between SQLite and PostgreSQL databases.

Usage:
    python scripts/verify_migration.py [--source-db /path/to/data.db] [--target-url postgresql://...]
"""

import sqlite3
import json
import argparse
from pathlib import Path
from datetime import datetime


def parse_args():
    parser = argparse.ArgumentParser(description="Verify SQLite to PostgreSQL migration")
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
    return parser.parse_args()


def verify_migration(sqlite_path: str, pg_url: str) -> bool:
    """Verify migration from SQLite to PostgreSQL."""
    print("🔍 Verifying SQLite to PostgreSQL Migration")
    print("=" * 60)
    print()

    # Connect to SQLite
    try:
        sqlite_conn = sqlite3.connect(sqlite_path)
        sqlite_conn.row_factory = sqlite3.Row
        sqlite_cursor = sqlite_conn.cursor()
    except sqlite3.Error as e:
        print(f"❌ Failed to connect to SQLite: {e}")
        return False

    # Connect to PostgreSQL
    try:
        import psycopg
        pg_conn = psycopg.connect(pg_url)
        pg_cursor = pg_conn.cursor()
    except Exception as e:
        print(f"❌ Failed to connect to PostgreSQL: {e}")
        sqlite_conn.close()
        return False

    all_ok = True

    try:
        # 1. Compare row counts
        print("1️⃣  Comparing Row Counts")
        print("-" * 60)

        tables = ["jobs", "market_data", "market_candles"]
        counts = {}

        for table in tables:
            sqlite_cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            sqlite_count = sqlite_cursor.fetchone()["cnt"]

            pg_cursor.execute(f"SELECT COUNT(*) FROM {table}")
            pg_count = pg_cursor.fetchone()[0]

            match = "✅" if sqlite_count == pg_count else "❌"
            counts[table] = (sqlite_count, pg_count)
            print(f"{match} {table:20} | SQLite: {sqlite_count:8} | PostgreSQL: {pg_count:8}")

            if sqlite_count != pg_count:
                all_ok = False

        print()

        # 2. Check schema integrity
        print("2️⃣  Checking Schema Integrity")
        print("-" * 60)

        # Jobs table
        sqlite_cursor.execute("PRAGMA table_info(jobs)")
        sqlite_jobs_cols = [row[1] for row in sqlite_cursor.fetchall()]

        pg_cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'jobs' ORDER BY ordinal_position
            """
        )
        pg_jobs_cols = [row[0] for row in pg_cursor.fetchall()]

        jobs_match = set(sqlite_jobs_cols) == set(pg_jobs_cols)
        print(f"{'✅' if jobs_match else '❌'} jobs table: {', '.join(sqlite_jobs_cols)}")

        if not jobs_match:
            print(f"   PostgreSQL: {', '.join(pg_jobs_cols)}")
            all_ok = False

        # Market data table
        sqlite_cursor.execute("PRAGMA table_info(market_data)")
        sqlite_md_cols = [row[1] for row in sqlite_cursor.fetchall()]

        pg_cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'market_data' ORDER BY ordinal_position
            """
        )
        pg_md_cols = [row[0] for row in pg_cursor.fetchall()]

        md_match = set(sqlite_md_cols) == set(pg_md_cols)
        print(f"{'✅' if md_match else '❌'} market_data table: {', '.join(sqlite_md_cols)}")

        if not md_match:
            print(f"   PostgreSQL: {', '.join(pg_md_cols)}")
            all_ok = False

        # Market candles table
        sqlite_cursor.execute("PRAGMA table_info(market_candles)")
        sqlite_mc_cols = [row[1] for row in sqlite_cursor.fetchall()]

        pg_cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'market_candles' ORDER BY ordinal_position
            """
        )
        pg_mc_cols = [row[0] for row in pg_cursor.fetchall()]

        mc_match = set(sqlite_mc_cols) == set(pg_mc_cols)
        print(f"{'✅' if mc_match else '❌'} market_candles table: {', '.join(sqlite_mc_cols)}")

        if not mc_match:
            print(f"   PostgreSQL: {', '.join(pg_mc_cols)}")
            all_ok = False

        print()

        # 3. Sample data verification
        if counts["jobs"][0] > 0:
            print("3️⃣  Verifying Sample Data (jobs)")
            print("-" * 60)

            sqlite_cursor.execute("SELECT * FROM jobs LIMIT 1")
            sqlite_job = sqlite_cursor.fetchone()

            pg_cursor.execute("SELECT * FROM jobs LIMIT 1")
            pg_row = pg_cursor.fetchone()

            if sqlite_job and pg_row:
                pg_columns = [desc[0] for desc in pg_cursor.description]
                pg_job = dict(zip(pg_columns, pg_row))

                # Compare key fields
                fields_to_check = ["id", "collector", "status"]
                sample_match = True

                for field in fields_to_check:
                    sqlite_val = sqlite_job[field]
                    pg_val = pg_job[field]
                    match = sqlite_val == pg_val
                    symbol = "✅" if match else "❌"
                    print(f"{symbol} {field:15}: '{sqlite_val}' == '{pg_val}'")
                    if not match:
                        sample_match = False

                if not sample_match:
                    all_ok = False

        print()

        # 4. Data completeness check
        print("4️⃣  Data Completeness")
        print("-" * 60)

        # Check for NULL values
        sqlite_cursor.execute("SELECT COUNT(*) FROM jobs WHERE id IS NULL")
        sqlite_null_jobs = sqlite_cursor.fetchone()[0]

        pg_cursor.execute("SELECT COUNT(*) FROM jobs WHERE id IS NULL")
        pg_null_jobs = pg_cursor.fetchone()[0]

        jobs_null_match = sqlite_null_jobs == pg_null_jobs
        print(
            f"{'✅' if jobs_null_match else '❌'} jobs with NULL id: "
            f"SQLite={sqlite_null_jobs}, PostgreSQL={pg_null_jobs}"
        )

        if not jobs_null_match:
            all_ok = False

        # 5. Index verification
        print()
        print("5️⃣  Index Verification")
        print("-" * 60)

        pg_cursor.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename IN ('jobs', 'market_data', 'market_candles')
            ORDER BY indexname
            """
        )
        indexes = [row[0] for row in pg_cursor.fetchall()]

        expected_indexes = [
            "idx_md_symbol",
            "idx_md_collector",
            "idx_mc_symbol",
            "idx_mc_collector",
            "idx_mc_time",
        ]

        print(f"Found {len(indexes)} indexes:")
        for idx in indexes:
            expected = idx in expected_indexes
            symbol = "✅" if expected else "⚠️"
            print(f"{symbol} {idx}")

        for expected_idx in expected_indexes:
            if expected_idx not in indexes:
                print(f"❌ Missing expected index: {expected_idx}")
                all_ok = False

        print()

        # 6. Summary
        print("=" * 60)
        if all_ok:
            print("✅ Migration verification PASSED")
            print()
            print("Summary:")
            for table, (sqlite_cnt, pg_cnt) in counts.items():
                print(f"  {table}: {sqlite_cnt} rows in both databases")
            return True
        else:
            print("❌ Migration verification FAILED")
            print()
            print("Issues found:")
            for table, (sqlite_cnt, pg_cnt) in counts.items():
                if sqlite_cnt != pg_cnt:
                    print(f"  Row count mismatch in {table}")
            return False

    except Exception as e:
        print(f"❌ Verification error: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    args = parse_args()

    if not Path(args.source_db).exists():
        print(f"❌ SQLite database not found: {args.source_db}")
        exit(1)

    success = verify_migration(args.source_db, args.target_url)
    exit(0 if success else 1)

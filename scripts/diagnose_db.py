#!/usr/bin/env python3
"""
SQLite Database Diagnosis and Recovery Script

This script diagnoses and attempts to recover the market data database
after Docker image updates or corruption.
"""

import os
import sys
import sqlite3
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

DB_PATH = Path(os.environ.get("DB_PATH", "/app/data/data.db"))


def log(message: str, level: str = "INFO"):
    """Print timestamped log message."""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] [{level}] {message}")


def check_file_status() -> dict:
    """Check database file existence, size, permissions, and modification time."""
    log("Checking database file status...")
    result = {
        "exists": False,
        "size_bytes": None,
        "size_mb": None,
        "permissions": None,
        "modified_at": None,
        "is_readable": False,
        "is_writable": False,
        "path": str(DB_PATH),
    }

    if not DB_PATH.exists():
        log(f"Database file does not exist: {DB_PATH}", "WARNING")
        return result

    result["exists"] = True
    result["size_bytes"] = DB_PATH.stat().st_size
    result["size_mb"] = round(result["size_bytes"] / (1024 * 1024), 2)
    result["permissions"] = oct(DB_PATH.stat().st_mode)[-3:]
    result["modified_at"] = datetime.fromtimestamp(
        DB_PATH.stat().st_mtime
    ).isoformat()
    result["is_readable"] = os.access(DB_PATH, os.R_OK)
    result["is_writable"] = os.access(DB_PATH, os.W_OK)

    log(f"File size: {result['size_mb']}MB")
    log(f"Modified: {result['modified_at']}")
    log(f"Readable: {result['is_readable']}, Writable: {result['is_writable']}")

    return result


def check_integrity() -> Tuple[bool, str]:
    """Check database integrity using PRAGMA integrity_check."""
    log("Running integrity check...")

    if not DB_PATH.exists():
        return False, "Database file does not exist"

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        result = cursor.fetchone()
        conn.close()

        if result and result[0] == "ok":
            log("Database integrity check passed")
            return True, "ok"
        else:
            message = str(result[0]) if result else "Unknown error"
            log(f"Database integrity check failed: {message}", "ERROR")
            return False, message
    except sqlite3.DatabaseError as e:
        log(f"Database error during integrity check: {e}", "ERROR")
        return False, str(e)
    except Exception as e:
        log(f"Unexpected error during integrity check: {e}", "ERROR")
        return False, str(e)


def check_structure() -> dict:
    """Check if database has required tables and structure."""
    log("Checking database structure...")
    result = {
        "has_jobs_table": False,
        "has_market_data_table": False,
        "has_market_candles_table": False,
        "tables": [],
        "job_count": 0,
        "market_data_count": 0,
        "market_candles_count": 0,
    }

    if not DB_PATH.exists():
        return result

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        cursor = conn.cursor()

        # Get all tables
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        result["tables"] = tables

        result["has_jobs_table"] = "jobs" in tables
        result["has_market_data_table"] = "market_data" in tables
        result["has_market_candles_table"] = "market_candles" in tables

        # Get row counts
        if result["has_jobs_table"]:
            cursor.execute("SELECT COUNT(*) FROM jobs")
            result["job_count"] = cursor.fetchone()[0]

        if result["has_market_data_table"]:
            cursor.execute("SELECT COUNT(*) FROM market_data")
            result["market_data_count"] = cursor.fetchone()[0]

        if result["has_market_candles_table"]:
            cursor.execute("SELECT COUNT(*) FROM market_candles")
            result["market_candles_count"] = cursor.fetchone()[0]

        conn.close()

        log(f"Tables found: {', '.join(tables)}")
        log(f"Jobs: {result['job_count']}, Market Data: {result['market_data_count']}, Candles: {result['market_candles_count']}")

        return result
    except Exception as e:
        log(f"Error checking database structure: {e}", "ERROR")
        return result


def attempt_recovery() -> Tuple[bool, str]:
    """Attempt to recover the database using VACUUM and other methods."""
    log("Attempting database recovery...", "INFO")

    if not DB_PATH.exists():
        log("Database does not exist - cannot recover", "ERROR")
        return False, "Database does not exist"

    # Create backup first
    backup_path = DB_PATH.with_suffix(f".backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    try:
        shutil.copy2(DB_PATH, backup_path)
        log(f"Created backup: {backup_path}")
    except Exception as e:
        log(f"Warning: Could not create backup: {e}", "WARNING")

    # Try VACUUM
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.execute("VACUUM")
        conn.close()
        log("VACUUM completed successfully")
        return True, "VACUUM successful"
    except sqlite3.DatabaseError as e:
        log(f"VACUUM failed: {e}", "ERROR")
        # Try to restore from backup
        if backup_path.exists():
            try:
                shutil.copy2(backup_path, DB_PATH)
                log(f"Restored from backup: {backup_path}")
                return False, f"VACUUM failed, restored backup. Error: {e}"
            except Exception as restore_error:
                log(f"Failed to restore backup: {restore_error}", "ERROR")
                return False, str(e)
        return False, str(e)


def reinitialize_db() -> bool:
    """Reinitialize the database with the expected schema."""
    log("Reinitializing database...")

    # Import the db module to use its initialization function
    try:
        # Add app directory to path
        app_path = Path(__file__).parent.parent / "app"
        sys.path.insert(0, str(app_path.parent))

        from app import db

        # Backup existing db if it exists
        if DB_PATH.exists():
            backup_path = DB_PATH.with_suffix(
                f".backup.before_reinit.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            try:
                shutil.copy2(DB_PATH, backup_path)
                log(f"Created pre-reinit backup: {backup_path}")
            except Exception as e:
                log(f"Warning: Could not create backup: {e}", "WARNING")

            # Remove the corrupted database
            DB_PATH.unlink()
            log("Removed corrupted database file")

        # Reinitialize
        db.init_db()
        log("Database reinitialized successfully")
        return True
    except Exception as e:
        log(f"Error during reinitialization: {e}", "ERROR")
        return False


def generate_report(
    file_status: dict,
    integrity: Tuple[bool, str],
    structure: dict,
    recovery: Optional[Tuple[bool, str]] = None,
) -> dict:
    """Generate a diagnostic report."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "database_path": str(DB_PATH),
        "file_status": file_status,
        "integrity_check": {
            "passed": integrity[0],
            "message": integrity[1],
        },
        "structure": structure,
        "recovery_attempted": recovery is not None,
        "recovery": None,
    }

    if recovery:
        report["recovery"] = {
            "success": recovery[0],
            "message": recovery[1],
        }

    return report


def main():
    """Main diagnostic and recovery routine."""
    log("Starting SQLite Database Diagnosis and Recovery")
    log(f"Database path: {DB_PATH}")

    # Step 1: Check file status
    file_status = check_file_status()

    # Step 2: Check integrity
    integrity_passed, integrity_msg = check_integrity()

    # Step 3: Check structure
    structure = check_structure()

    # Step 4: Determine recovery strategy
    recovery_result = None
    if not integrity_passed or not structure["has_jobs_table"]:
        log("Database is corrupted or has missing tables", "WARNING")

        if file_status["exists"] and file_status["size_bytes"] > 0:
            recovery_result = attempt_recovery()

        if not recovery_result or not recovery_result[0]:
            log("Recovery from corruption failed, reinitializing database", "WARNING")
            reinit_success = reinitialize_db()
            recovery_result = (reinit_success, "Database reinitialized from schema")

    # Step 5: Generate and display report
    report = generate_report(file_status, (integrity_passed, integrity_msg), structure, recovery_result)

    log("\n" + "=" * 80)
    log("DIAGNOSIS REPORT")
    log("=" * 80)
    print(json.dumps(report, indent=2, default=str))

    # Return exit code
    if report["integrity_check"]["passed"]:
        log("Database is healthy", "INFO")
        return 0
    else:
        log("Database issues detected - review report above", "ERROR")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Database monitoring and health checks for SQLite and PostgreSQL."""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_TYPE = os.environ.get("DB_TYPE", "postgres").lower()
DB_PATH = os.environ.get("DB_PATH", "data.db")
POSTGRES_URL = os.environ.get("DATABASE_URL", "").strip()


def check_sqlite_health() -> dict[str, Any]:
    """Check SQLite database health."""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()

        # Integrity check
        cursor.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        is_healthy = integrity == "ok"

        # Get file size
        import os
        file_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0

        # Table counts
        cursor.execute("SELECT COUNT(*) FROM jobs")
        jobs_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM market_data")
        market_data_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM market_candles")
        candles_count = cursor.fetchone()[0]

        # WAL status
        cursor.execute("PRAGMA journal_mode")
        journal_mode = cursor.fetchone()[0]

        conn.close()

        return {
            "type": "sqlite",
            "healthy": is_healthy,
            "integrity_check": integrity,
            "file_size_bytes": file_size,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "tables": {
                "jobs": jobs_count,
                "market_data": market_data_count,
                "market_candles": candles_count,
            },
            "total_records": jobs_count + market_data_count + candles_count,
            "journal_mode": journal_mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        return {
            "type": "sqlite",
            "healthy": False,
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def check_postgres_health() -> dict[str, Any]:
    """Check PostgreSQL database health."""
    try:
        import psycopg

        if not POSTGRES_URL:
            raise RuntimeError("DATABASE_URL is required when DB_TYPE=postgres")
        conn = psycopg.connect(
            POSTGRES_URL,
            connect_timeout=10,
            application_name="tradingview-health",
        )
        cursor = conn.cursor()

        # Server version
        cursor.execute("SELECT version()")
        version = cursor.fetchone()[0]

        # Connection info
        cursor.execute("SELECT current_user, current_database()")
        user, database = cursor.fetchone()

        # Table counts
        cursor.execute("SELECT COUNT(*) FROM jobs")
        jobs_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM market_data")
        market_data_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM market_candles")
        candles_count = cursor.fetchone()[0]

        # Database size
        cursor.execute(
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        )
        db_size = cursor.fetchone()[0]

        # Active connections
        cursor.execute(
            "SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database()"
        )
        active_connections = cursor.fetchone()[0]

        # Get numeric size in bytes for consistency
        cursor.execute("SELECT pg_database_size(current_database())")
        db_size_bytes = cursor.fetchone()[0]

        # Avoid issuing a failing query when the optional extension is absent.
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')"
        )
        if cursor.fetchone()[0]:
            cursor.execute(
                "SELECT COUNT(*) FROM pg_stat_statements WHERE mean_exec_time > 1000"
            )
            slow_queries = cursor.fetchone()[0]
        else:
            slow_queries = None

        conn.close()

        return {
            "type": "postgres",
            "healthy": True,
            "version": version,
            "user": user,
            "database": database,
            "tables": {
                "jobs": jobs_count,
                "market_data": market_data_count,
                "market_candles": candles_count,
            },
            "total_records": jobs_count + market_data_count + candles_count,
            "database_size": db_size,
            "database_size_bytes": db_size_bytes,
            "active_connections": active_connections,
            "slow_queries_detected": slow_queries > 0 if slow_queries is not None else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        return {
            "type": "postgres",
            "healthy": False,
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def get_database_health() -> dict[str, Any]:
    """Get health status for the current database."""
    if DB_TYPE == "postgres":
        return check_postgres_health()
    if DB_TYPE == "sqlite":
        return check_sqlite_health()
    return {
        "type": DB_TYPE,
        "healthy": False,
        "error": f"Unsupported DB_TYPE: {DB_TYPE!r}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_database_stats() -> dict[str, Any]:
    """Get detailed database statistics."""
    health = get_database_health()

    if not health.get("healthy"):
        return {
            "status": "unhealthy",
            "database": DB_TYPE,
            "error": health.get("error"),
            "timestamp": health.get("timestamp"),
        }

    stats = {
        "status": "healthy",
        "database": DB_TYPE,
        "health": health,
    }

    # Add database-specific stats
    if DB_TYPE == "postgres":
        try:
            import psycopg

            conn = psycopg.connect(POSTGRES_URL)
            cursor = conn.cursor()

            # Index usage
            cursor.execute(
                """
                SELECT schemaname, tablename, indexname, idx_scan
                FROM pg_stat_user_indexes
                ORDER BY idx_scan DESC
                """
            )
            indexes = cursor.fetchall()
            stats["indexes"] = [
                {
                    "schema": idx[0],
                    "table": idx[1],
                    "index": idx[2],
                    "scans": idx[3],
                }
                for idx in indexes
            ]

            # Table sizes
            cursor.execute(
                """
                SELECT tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename))
                FROM pg_tables
                WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
                """
            )
            tables = cursor.fetchall()
            stats["table_sizes"] = [
                {"table": t[0], "size": t[1]} for t in tables
            ]

            # Cache hit ratio
            cursor.execute(
                """
                SELECT sum(heap_blks_read) / (sum(heap_blks_read) + sum(heap_blks_hit)) as ratio
                FROM pg_statio_user_tables
                """
            )
            result = cursor.fetchone()
            if result[0] is not None:
                stats["cache_hit_ratio"] = round(1.0 - float(result[0]), 4)

            conn.close()

        except Exception as e:
            stats["error"] = f"Failed to fetch detailed stats: {str(e)}"

    stats["timestamp"] = datetime.now(timezone.utc).isoformat()
    return stats


def get_migration_readiness() -> dict[str, Any]:
    """Check if database is ready for migration."""
    current_health = get_database_health()

    if DB_TYPE == "sqlite":
        return {
            "ready": current_health.get("healthy", False),
            "database": "sqlite",
            "checks": {
                "integrity": current_health.get("integrity_check") == "ok",
                "accessible": True if current_health.get("healthy") else False,
                "records": current_health.get("total_records", 0),
                "size_mb": current_health.get("file_size_mb", 0),
            },
            "recommendation": "SQLite healthy and ready for migration" if current_health.get("healthy") else "SQLite has issues - check integrity",
        }

    if DB_TYPE == "postgres":
        return {
            "ready": current_health.get("healthy", False),
            "database": "postgres",
            "checks": {
                "connection": current_health.get("healthy", False),
                "records": current_health.get("total_records", 0),
                "active_connections": current_health.get("active_connections", 0),
            },
            "recommendation": "PostgreSQL ready and accepting connections" if current_health.get("healthy") else "PostgreSQL connection failed",
        }

    return {
        "ready": False,
        "database": DB_TYPE,
        "checks": {"configuration": False},
        "recommendation": f"Unsupported DB_TYPE: {DB_TYPE!r}",
    }

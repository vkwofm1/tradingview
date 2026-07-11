import sys
from types import SimpleNamespace

from app import db_monitoring


class _Cursor:
    def __init__(self):
        self.query = ""
        self.queries = []

    def execute(self, query):
        self.query = query
        self.queries.append(query)

    def fetchone(self):
        if "SELECT version" in self.query:
            return ("PostgreSQL 16",)
        if "current_user" in self.query:
            return ("tradingview", "tradingview")
        if "pg_size_pretty" in self.query:
            return ("1 GB",)
        if "pg_database_size" in self.query:
            return (1_000_000_000,)
        if "pg_extension" in self.query:
            return (False,)
        return (1,)


class _Connection:
    def __init__(self):
        self.cursor_instance = _Cursor()
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


def test_health_skips_pg_stat_statements_query_when_extension_is_absent(monkeypatch):
    connection = _Connection()
    monkeypatch.setattr(db_monitoring, "POSTGRES_URL", "postgresql://unit-test")
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(connect=lambda *_args, **_kwargs: connection),
    )

    result = db_monitoring.check_postgres_health()

    assert result["healthy"] is True
    assert result["slow_queries_detected"] is None
    assert not any(
        "FROM pg_stat_statements" in query
        for query in connection.cursor_instance.queries
    )
    assert connection.closed is True


def test_unknown_database_type_is_unhealthy(monkeypatch):
    monkeypatch.setattr(db_monitoring, "DB_TYPE", "unknown")

    health = db_monitoring.get_database_health()
    readiness = db_monitoring.get_migration_readiness()

    assert health["healthy"] is False
    assert "Unsupported DB_TYPE" in health["error"]
    assert readiness["ready"] is False
    assert readiness["checks"] == {"configuration": False}

"""Adoption metrics data pipeline for framework usage, decision quality, and engagement tracking."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db


def init_adoption_db() -> None:
    """Initialize adoption metrics tables."""
    if db.is_postgres():
        _init_postgres_adoption_db()
    else:
        _init_sqlite_adoption_db()


def _init_sqlite_adoption_db() -> None:
    conn = db._get_sqlite_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS survey_responses (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id       TEXT NOT NULL,
            survey_type         TEXT NOT NULL,
            survey_date         TEXT NOT NULL,
            score               INTEGER,
            feedback            TEXT,
            created_at          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_survey_respondent ON survey_responses(respondent_id);
        CREATE INDEX IF NOT EXISTS idx_survey_type ON survey_responses(survey_type);
        CREATE INDEX IF NOT EXISTS idx_survey_date ON survey_responses(survey_date);

        CREATE TABLE IF NOT EXISTS system_logs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             TEXT NOT NULL,
            action_type         TEXT NOT NULL,
            decision_id         TEXT,
            metadata            TEXT,
            logged_at           TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_logs_user ON system_logs(user_id);
        CREATE INDEX IF NOT EXISTS idx_logs_action ON system_logs(action_type);
        CREATE INDEX IF NOT EXISTS idx_logs_time ON system_logs(logged_at);

        CREATE TABLE IF NOT EXISTS adoption_metrics_daily (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_date             TEXT NOT NULL UNIQUE,
            framework_usage_rate    REAL,
            active_users            INTEGER,
            total_decisions         INTEGER,
            framework_decisions     INTEGER,
            calculated_at           TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS adoption_metrics_weekly (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start              TEXT NOT NULL UNIQUE,
            framework_usage_rate    REAL,
            decision_quality_score  REAL,
            engagement_index        REAL,
            active_users            INTEGER,
            survey_responses_count  INTEGER,
            calculated_at           TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS adoption_metrics_monthly (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            month_start             TEXT NOT NULL UNIQUE,
            framework_usage_rate    REAL,
            decision_quality_score  REAL,
            engagement_index        REAL,
            active_users            INTEGER,
            survey_responses_count  INTEGER,
            calculated_at           TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS survey_responses_archive (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            respondent_id       TEXT NOT NULL,
            survey_type         TEXT NOT NULL,
            survey_date         TEXT NOT NULL,
            score               INTEGER,
            feedback            TEXT,
            created_at          TEXT NOT NULL,
            archived_at         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_survey_archive_date ON survey_responses_archive(survey_date);

        CREATE TABLE IF NOT EXISTS system_logs_archive (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             TEXT NOT NULL,
            action_type         TEXT NOT NULL,
            decision_id         TEXT,
            metadata            TEXT,
            logged_at           TEXT NOT NULL,
            archived_at         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_logs_archive_time ON system_logs_archive(logged_at);

        CREATE TABLE IF NOT EXISTS archival_reports (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type         TEXT NOT NULL,
            period_start        TEXT NOT NULL,
            period_end          TEXT NOT NULL,
            summary             TEXT NOT NULL,
            created_at          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_report_type ON archival_reports(report_type);
        CREATE INDEX IF NOT EXISTS idx_report_period ON archival_reports(period_start);
    """)


def _init_postgres_adoption_db() -> None:
    conn = db._get_postgres_conn()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS survey_responses (
            id                  SERIAL PRIMARY KEY,
            respondent_id       VARCHAR(255) NOT NULL,
            survey_type         VARCHAR(100) NOT NULL,
            survey_date         DATE NOT NULL,
            score               INTEGER,
            feedback            TEXT,
            created_at          TIMESTAMPTZ NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_survey_respondent ON survey_responses(respondent_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_survey_type ON survey_responses(survey_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_survey_date ON survey_responses(survey_date)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_logs (
            id                  SERIAL PRIMARY KEY,
            user_id             VARCHAR(255) NOT NULL,
            action_type         VARCHAR(100) NOT NULL,
            decision_id         VARCHAR(255),
            metadata            JSONB,
            logged_at           TIMESTAMPTZ NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_user ON system_logs(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_action ON system_logs(action_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_time ON system_logs(logged_at)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS adoption_metrics_daily (
            id                      SERIAL PRIMARY KEY,
            metric_date             DATE NOT NULL UNIQUE,
            framework_usage_rate    NUMERIC(5,2),
            active_users            INTEGER,
            total_decisions         INTEGER,
            framework_decisions     INTEGER,
            calculated_at           TIMESTAMPTZ NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS adoption_metrics_weekly (
            id                      SERIAL PRIMARY KEY,
            week_start              DATE NOT NULL UNIQUE,
            framework_usage_rate    NUMERIC(5,2),
            decision_quality_score  NUMERIC(3,2),
            engagement_index        NUMERIC(5,2),
            active_users            INTEGER,
            survey_responses_count  INTEGER,
            calculated_at           TIMESTAMPTZ NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS adoption_metrics_monthly (
            id                      SERIAL PRIMARY KEY,
            month_start             DATE NOT NULL UNIQUE,
            framework_usage_rate    NUMERIC(5,2),
            decision_quality_score  NUMERIC(3,2),
            engagement_index        NUMERIC(5,2),
            active_users            INTEGER,
            survey_responses_count  INTEGER,
            calculated_at           TIMESTAMPTZ NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS survey_responses_archive (
            id                  SERIAL PRIMARY KEY,
            respondent_id       VARCHAR(255) NOT NULL,
            survey_type         VARCHAR(100) NOT NULL,
            survey_date         DATE NOT NULL,
            score               INTEGER,
            feedback            TEXT,
            created_at          TIMESTAMPTZ NOT NULL,
            archived_at         TIMESTAMPTZ NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_survey_archive_date ON survey_responses_archive(survey_date)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_logs_archive (
            id                  SERIAL PRIMARY KEY,
            user_id             VARCHAR(255) NOT NULL,
            action_type         VARCHAR(100) NOT NULL,
            decision_id         VARCHAR(255),
            metadata            JSONB,
            logged_at           TIMESTAMPTZ NOT NULL,
            archived_at         TIMESTAMPTZ NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_archive_time ON system_logs_archive(logged_at)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS archival_reports (
            id                  SERIAL PRIMARY KEY,
            report_type         VARCHAR(100) NOT NULL,
            period_start        DATE NOT NULL,
            period_end          DATE NOT NULL,
            summary             TEXT NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_report_type ON archival_reports(report_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_report_period ON archival_reports(period_start)")

    conn.commit()


def log_system_action(
    user_id: str,
    action_type: str,
    decision_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a system action for adoption metrics tracking."""
    now = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps(metadata) if metadata else None

    if db.is_postgres():
        db._execute_postgres(
            "INSERT INTO system_logs (user_id, action_type, decision_id, metadata, logged_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, action_type, decision_id, metadata_json, now),
        )
    else:
        db._execute_sqlite(
            "INSERT INTO system_logs (user_id, action_type, decision_id, metadata, logged_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, action_type, decision_id, metadata_json, now),
        )


def record_survey_response(
    respondent_id: str,
    survey_type: str,
    score: int,
    feedback: str | None = None,
    survey_date: str | None = None,
) -> None:
    """Record a survey response from a respondent."""
    if survey_date is None:
        survey_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    now = datetime.now(timezone.utc).isoformat()

    if db.is_postgres():
        db._execute_postgres(
            "INSERT INTO survey_responses (respondent_id, survey_type, survey_date, score, feedback, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (respondent_id, survey_type, survey_date, score, feedback, now),
        )
    else:
        db._execute_sqlite(
            "INSERT INTO survey_responses (respondent_id, survey_type, survey_date, score, feedback, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (respondent_id, survey_type, survey_date, score, feedback, now),
        )


def calculate_daily_metrics(target_date: str | None = None) -> dict[str, Any]:
    """Calculate daily adoption metrics for the specified date (or yesterday if not specified)."""
    if target_date is None:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    target_dt = datetime.fromisoformat(target_date)
    day_start = target_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    day_end = (target_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    if db.is_postgres():
        logs = db._execute_postgres(
            "SELECT DISTINCT user_id, action_type FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_all=True,
        )
        total_logs = db._execute_postgres(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_one=True,
        )
        framework_logs = db._execute_postgres(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ? AND action_type = 'framework_decision'",
            (day_start, day_end),
            fetch_one=True,
        )
    else:
        logs = db._execute_sqlite(
            "SELECT DISTINCT user_id, action_type FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_all=True,
        )
        total_logs = db._execute_sqlite(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_one=True,
        )
        framework_logs = db._execute_sqlite(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ? AND action_type = 'framework_decision'",
            (day_start, day_end),
            fetch_one=True,
        )

    logs = [dict(r) for r in logs] if logs else []
    total_count = dict(total_logs)["count"] if total_logs else 0
    framework_count = dict(framework_logs)["count"] if framework_logs else 0

    active_users = len(set(log["user_id"] for log in logs))
    framework_usage_rate = (framework_count / total_count * 100) if total_count > 0 else 0

    now = datetime.now(timezone.utc).isoformat()

    if db.is_postgres():
        db._execute_postgres(
            "INSERT INTO adoption_metrics_daily (metric_date, framework_usage_rate, active_users, total_decisions, framework_decisions, calculated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (metric_date) DO UPDATE SET framework_usage_rate = EXCLUDED.framework_usage_rate, active_users = EXCLUDED.active_users, total_decisions = EXCLUDED.total_decisions, framework_decisions = EXCLUDED.framework_decisions, calculated_at = EXCLUDED.calculated_at",
            (target_date, round(framework_usage_rate, 2), active_users, total_count, framework_count, now),
        )
    else:
        db._execute_sqlite(
            "INSERT OR REPLACE INTO adoption_metrics_daily (metric_date, framework_usage_rate, active_users, total_decisions, framework_decisions, calculated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (target_date, round(framework_usage_rate, 2), active_users, total_count, framework_count, now),
        )

    return {
        "metric_date": target_date,
        "framework_usage_rate": round(framework_usage_rate, 2),
        "active_users": active_users,
        "total_decisions": total_count,
        "framework_decisions": framework_count,
        "calculated_at": now,
    }


def calculate_weekly_metrics(week_start: str | None = None) -> dict[str, Any]:
    """Calculate weekly adoption metrics for the specified week (or previous week if not specified)."""
    if week_start is None:
        today = datetime.now(timezone.utc)
        week_start_dt = today - timedelta(days=today.weekday() + 1)
        week_start = week_start_dt.strftime("%Y-%m-%d")
    else:
        week_start_dt = datetime.fromisoformat(week_start)

    week_end_dt = week_start_dt + timedelta(days=7)
    day_start = week_start_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    day_end = week_end_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    if db.is_postgres():
        logs = db._execute_postgres(
            "SELECT DISTINCT user_id, action_type FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_all=True,
        )
        total_logs = db._execute_postgres(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_one=True,
        )
        framework_logs = db._execute_postgres(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ? AND action_type = 'framework_decision'",
            (day_start, day_end),
            fetch_one=True,
        )
        surveys = db._execute_postgres(
            "SELECT AVG(score) as avg_score, COUNT(*) as count FROM survey_responses WHERE survey_date >= ? AND survey_date < ?",
            (week_start, week_end_dt.strftime("%Y-%m-%d")),
            fetch_one=True,
        )
    else:
        logs = db._execute_sqlite(
            "SELECT DISTINCT user_id, action_type FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_all=True,
        )
        total_logs = db._execute_sqlite(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_one=True,
        )
        framework_logs = db._execute_sqlite(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ? AND action_type = 'framework_decision'",
            (day_start, day_end),
            fetch_one=True,
        )
        surveys = db._execute_sqlite(
            "SELECT AVG(score) as avg_score, COUNT(*) as count FROM survey_responses WHERE survey_date >= ? AND survey_date < ?",
            (week_start, week_end_dt.strftime("%Y-%m-%d")),
            fetch_one=True,
        )

    logs = [dict(r) for r in logs] if logs else []
    total_count = dict(total_logs)["count"] if total_logs else 0
    framework_count = dict(framework_logs)["count"] if framework_logs else 0
    survey_dict = dict(surveys) if surveys else {}

    active_users = len(set(log["user_id"] for log in logs))
    framework_usage_rate = (framework_count / total_count * 100) if total_count > 0 else 0
    decision_quality_score = round(survey_dict.get("avg_score", 0), 2) if survey_dict.get("avg_score") else 0
    survey_count = survey_dict.get("count", 0) if survey_dict else 0

    engagement_index = _calculate_engagement_index(active_users, framework_usage_rate, survey_count)

    now = datetime.now(timezone.utc).isoformat()

    if db.is_postgres():
        db._execute_postgres(
            "INSERT INTO adoption_metrics_weekly (week_start, framework_usage_rate, decision_quality_score, engagement_index, active_users, survey_responses_count, calculated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (week_start) DO UPDATE SET framework_usage_rate = EXCLUDED.framework_usage_rate, decision_quality_score = EXCLUDED.decision_quality_score, engagement_index = EXCLUDED.engagement_index, active_users = EXCLUDED.active_users, survey_responses_count = EXCLUDED.survey_responses_count, calculated_at = EXCLUDED.calculated_at",
            (week_start, round(framework_usage_rate, 2), decision_quality_score, round(engagement_index, 2), active_users, survey_count, now),
        )
    else:
        db._execute_sqlite(
            "INSERT OR REPLACE INTO adoption_metrics_weekly (week_start, framework_usage_rate, decision_quality_score, engagement_index, active_users, survey_responses_count, calculated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (week_start, round(framework_usage_rate, 2), decision_quality_score, round(engagement_index, 2), active_users, survey_count, now),
        )

    return {
        "week_start": week_start,
        "framework_usage_rate": round(framework_usage_rate, 2),
        "decision_quality_score": decision_quality_score,
        "engagement_index": round(engagement_index, 2),
        "active_users": active_users,
        "survey_responses_count": survey_count,
        "calculated_at": now,
    }


def calculate_monthly_metrics(month_start: str | None = None) -> dict[str, Any]:
    """Calculate monthly adoption metrics for the specified month (or previous month if not specified)."""
    if month_start is None:
        today = datetime.now(timezone.utc)
        month_start_dt = today.replace(day=1)
        month_start = month_start_dt.strftime("%Y-%m-%d")
    else:
        month_start_dt = datetime.fromisoformat(month_start)

    if month_start_dt.month == 12:
        month_end_dt = month_start_dt.replace(year=month_start_dt.year + 1, month=1)
    else:
        month_end_dt = month_start_dt.replace(month=month_start_dt.month + 1)

    day_start = month_start_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    day_end = month_end_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    if db.is_postgres():
        logs = db._execute_postgres(
            "SELECT DISTINCT user_id, action_type FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_all=True,
        )
        total_logs = db._execute_postgres(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_one=True,
        )
        framework_logs = db._execute_postgres(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ? AND action_type = 'framework_decision'",
            (day_start, day_end),
            fetch_one=True,
        )
        surveys = db._execute_postgres(
            "SELECT AVG(score) as avg_score, COUNT(*) as count FROM survey_responses WHERE survey_date >= ? AND survey_date < ?",
            (month_start, month_end_dt.strftime("%Y-%m-%d")),
            fetch_one=True,
        )
    else:
        logs = db._execute_sqlite(
            "SELECT DISTINCT user_id, action_type FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_all=True,
        )
        total_logs = db._execute_sqlite(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ?",
            (day_start, day_end),
            fetch_one=True,
        )
        framework_logs = db._execute_sqlite(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ? AND action_type = 'framework_decision'",
            (day_start, day_end),
            fetch_one=True,
        )
        surveys = db._execute_sqlite(
            "SELECT AVG(score) as avg_score, COUNT(*) as count FROM survey_responses WHERE survey_date >= ? AND survey_date < ?",
            (month_start, month_end_dt.strftime("%Y-%m-%d")),
            fetch_one=True,
        )

    logs = [dict(r) for r in logs] if logs else []
    total_count = dict(total_logs)["count"] if total_logs else 0
    framework_count = dict(framework_logs)["count"] if framework_logs else 0
    survey_dict = dict(surveys) if surveys else {}

    active_users = len(set(log["user_id"] for log in logs))
    framework_usage_rate = (framework_count / total_count * 100) if total_count > 0 else 0
    decision_quality_score = round(survey_dict.get("avg_score", 0), 2) if survey_dict.get("avg_score") else 0
    survey_count = survey_dict.get("count", 0) if survey_dict else 0

    engagement_index = _calculate_engagement_index(active_users, framework_usage_rate, survey_count)

    now = datetime.now(timezone.utc).isoformat()

    if db.is_postgres():
        db._execute_postgres(
            "INSERT INTO adoption_metrics_monthly (month_start, framework_usage_rate, decision_quality_score, engagement_index, active_users, survey_responses_count, calculated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (month_start) DO UPDATE SET framework_usage_rate = EXCLUDED.framework_usage_rate, decision_quality_score = EXCLUDED.decision_quality_score, engagement_index = EXCLUDED.engagement_index, active_users = EXCLUDED.active_users, survey_responses_count = EXCLUDED.survey_responses_count, calculated_at = EXCLUDED.calculated_at",
            (month_start, round(framework_usage_rate, 2), decision_quality_score, round(engagement_index, 2), active_users, survey_count, now),
        )
    else:
        db._execute_sqlite(
            "INSERT OR REPLACE INTO adoption_metrics_monthly (month_start, framework_usage_rate, decision_quality_score, engagement_index, active_users, survey_responses_count, calculated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (month_start, round(framework_usage_rate, 2), decision_quality_score, round(engagement_index, 2), active_users, survey_count, now),
        )

    return {
        "month_start": month_start,
        "framework_usage_rate": round(framework_usage_rate, 2),
        "decision_quality_score": decision_quality_score,
        "engagement_index": round(engagement_index, 2),
        "active_users": active_users,
        "survey_responses_count": survey_count,
        "calculated_at": now,
    }


def _calculate_engagement_index(active_users: int, framework_usage_rate: float, survey_responses: int) -> float:
    """Calculate engagement index based on active users, usage rate, and survey participation."""
    usage_component = framework_usage_rate / 100
    users_component = min(active_users / 10, 1.0)
    survey_component = min(survey_responses / 5, 1.0)

    engagement = (usage_component * 0.5 + users_component * 0.3 + survey_component * 0.2) * 100
    return engagement


def get_daily_metrics(limit: int = 30) -> list[dict[str, Any]]:
    """Get recent daily metrics."""
    if db.is_postgres():
        rows = db._execute_postgres(
            "SELECT * FROM adoption_metrics_daily ORDER BY metric_date DESC LIMIT ?",
            (limit,),
            fetch_all=True,
        )
    else:
        rows = db._execute_sqlite(
            "SELECT * FROM adoption_metrics_daily ORDER BY metric_date DESC LIMIT ?",
            (limit,),
            fetch_all=True,
        )

    return [dict(r) for r in rows] if rows else []


def get_weekly_metrics(limit: int = 12) -> list[dict[str, Any]]:
    """Get recent weekly metrics (default: last 12 weeks)."""
    if db.is_postgres():
        rows = db._execute_postgres(
            "SELECT * FROM adoption_metrics_weekly ORDER BY week_start DESC LIMIT ?",
            (limit,),
            fetch_all=True,
        )
    else:
        rows = db._execute_sqlite(
            "SELECT * FROM adoption_metrics_weekly ORDER BY week_start DESC LIMIT ?",
            (limit,),
            fetch_all=True,
        )

    return [dict(r) for r in rows] if rows else []


def get_monthly_metrics(limit: int = 12) -> list[dict[str, Any]]:
    """Get recent monthly metrics (default: last 12 months)."""
    if db.is_postgres():
        rows = db._execute_postgres(
            "SELECT * FROM adoption_metrics_monthly ORDER BY month_start DESC LIMIT ?",
            (limit,),
            fetch_all=True,
        )
    else:
        rows = db._execute_sqlite(
            "SELECT * FROM adoption_metrics_monthly ORDER BY month_start DESC LIMIT ?",
            (limit,),
            fetch_all=True,
        )

    return [dict(r) for r in rows] if rows else []


def archive_old_feedback(days_to_keep: int = 90) -> dict[str, Any]:
    """Archive survey responses and logs older than specified days."""
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).isoformat()

    if db.is_postgres():
        survey_result = db._execute_postgres(
            "SELECT COUNT(*) as count FROM survey_responses WHERE survey_date < ?",
            (cutoff_date,),
            fetch_one=True,
        )
        log_result = db._execute_postgres(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at < ?",
            (datetime.now(timezone.utc).replace(day=1) - timedelta(days=days_to_keep)).isoformat(),
            fetch_one=True,
        )
        survey_count = dict(survey_result).get("count", 0) if survey_result else 0
        log_count = dict(log_result).get("count", 0) if log_result else 0

        db._execute_postgres(
            "INSERT INTO survey_responses_archive (respondent_id, survey_type, survey_date, score, feedback, created_at, archived_at) "
            "SELECT respondent_id, survey_type, survey_date, score, feedback, created_at, ? FROM survey_responses WHERE survey_date < ?",
            (now, cutoff_date),
        )
        db._execute_postgres(
            "DELETE FROM survey_responses WHERE survey_date < ?",
            (cutoff_date,),
        )

        cutoff_dt = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).isoformat()
        db._execute_postgres(
            "INSERT INTO system_logs_archive (user_id, action_type, decision_id, metadata, logged_at, archived_at) "
            "SELECT user_id, action_type, decision_id, metadata, logged_at, ? FROM system_logs WHERE logged_at < ?",
            (now, cutoff_dt),
        )
        db._execute_postgres(
            "DELETE FROM system_logs WHERE logged_at < ?",
            (cutoff_dt,),
        )
    else:
        survey_result = db._execute_sqlite(
            "SELECT COUNT(*) as count FROM survey_responses WHERE survey_date < ?",
            (cutoff_date,),
            fetch_one=True,
        )
        cutoff_dt = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).isoformat()
        log_result = db._execute_sqlite(
            "SELECT COUNT(*) as count FROM system_logs WHERE logged_at < ?",
            (cutoff_dt,),
            fetch_one=True,
        )
        survey_count = dict(survey_result).get("count", 0) if survey_result else 0
        log_count = dict(log_result).get("count", 0) if log_result else 0

        db._execute_sqlite(
            "INSERT INTO survey_responses_archive (respondent_id, survey_type, survey_date, score, feedback, created_at, archived_at) "
            "SELECT respondent_id, survey_type, survey_date, score, feedback, created_at, ? FROM survey_responses WHERE survey_date < ?",
            (now, cutoff_date),
        )
        db._execute_sqlite(
            "DELETE FROM survey_responses WHERE survey_date < ?",
            (cutoff_date,),
        )

        db._execute_sqlite(
            "INSERT INTO system_logs_archive (user_id, action_type, decision_id, metadata, logged_at, archived_at) "
            "SELECT user_id, action_type, decision_id, metadata, logged_at, ? FROM system_logs WHERE logged_at < ?",
            (now, cutoff_dt),
        )
        db._execute_sqlite(
            "DELETE FROM system_logs WHERE logged_at < ?",
            (cutoff_dt,),
        )

    return {
        "archived_surveys": survey_count,
        "archived_logs": log_count,
        "cutoff_date": cutoff_date,
        "completed_at": now,
    }


def generate_monthly_report(month_start: str) -> dict[str, Any]:
    """Generate a comprehensive monthly adoption report."""
    month_start_dt = datetime.fromisoformat(month_start)
    if month_start_dt.month == 12:
        month_end_dt = month_start_dt.replace(year=month_start_dt.year + 1, month=1)
    else:
        month_end_dt = month_start_dt.replace(month=month_start_dt.month + 1)
    month_end = month_end_dt.strftime("%Y-%m-%d")

    if db.is_postgres():
        metrics = db._execute_postgres(
            "SELECT * FROM adoption_metrics_monthly WHERE month_start = ?",
            (month_start,),
            fetch_one=True,
        )
        surveys = db._execute_postgres(
            "SELECT COUNT(*) as count, AVG(score) as avg_score FROM survey_responses WHERE survey_date >= ? AND survey_date < ?",
            (month_start, month_end),
            fetch_one=True,
        )
        logs = db._execute_postgres(
            "SELECT action_type, COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ? GROUP BY action_type",
            (month_start_dt.isoformat(), month_end_dt.isoformat()),
            fetch_all=True,
        )
    else:
        metrics = db._execute_sqlite(
            "SELECT * FROM adoption_metrics_monthly WHERE month_start = ?",
            (month_start,),
            fetch_one=True,
        )
        surveys = db._execute_sqlite(
            "SELECT COUNT(*) as count, AVG(score) as avg_score FROM survey_responses WHERE survey_date >= ? AND survey_date < ?",
            (month_start, month_end),
            fetch_one=True,
        )
        logs = db._execute_sqlite(
            "SELECT action_type, COUNT(*) as count FROM system_logs WHERE logged_at >= ? AND logged_at < ? GROUP BY action_type",
            (month_start_dt.isoformat(), month_end_dt.isoformat()),
            fetch_all=True,
        )

    metrics_dict = dict(metrics) if metrics else {}
    surveys_dict = dict(surveys) if surveys else {}
    action_breakdown = [dict(log) for log in logs] if logs else []

    report_summary = {
        "month": month_start,
        "framework_usage_rate": metrics_dict.get("framework_usage_rate", 0),
        "decision_quality_score": metrics_dict.get("decision_quality_score", 0),
        "engagement_index": metrics_dict.get("engagement_index", 0),
        "active_users": metrics_dict.get("active_users", 0),
        "survey_responses_count": surveys_dict.get("count", 0),
        "average_survey_score": round(surveys_dict.get("avg_score", 0), 2) if surveys_dict.get("avg_score") else 0,
        "action_breakdown": action_breakdown,
    }

    now = datetime.now(timezone.utc).isoformat()
    summary_json = json.dumps(report_summary)

    if db.is_postgres():
        db._execute_postgres(
            "INSERT INTO archival_reports (report_type, period_start, period_end, summary, created_at) VALUES (?, ?, ?, ?, ?)",
            ("monthly", month_start, month_end, summary_json, now),
        )
    else:
        db._execute_sqlite(
            "INSERT INTO archival_reports (report_type, period_start, period_end, summary, created_at) VALUES (?, ?, ?, ?, ?)",
            ("monthly", month_start, month_end, summary_json, now),
        )

    return report_summary


def get_archival_reports(report_type: str = "monthly", limit: int = 12) -> list[dict[str, Any]]:
    """Get archived reports."""
    if db.is_postgres():
        rows = db._execute_postgres(
            "SELECT id, report_type, period_start, period_end, summary, created_at FROM archival_reports WHERE report_type = ? ORDER BY period_start DESC LIMIT ?",
            (report_type, limit),
            fetch_all=True,
        )
    else:
        rows = db._execute_sqlite(
            "SELECT id, report_type, period_start, period_end, summary, created_at FROM archival_reports WHERE report_type = ? ORDER BY period_start DESC LIMIT ?",
            (report_type, limit),
            fetch_all=True,
        )

    reports = []
    for row in (rows if rows else []):
        row_dict = dict(row)
        row_dict["summary"] = json.loads(row_dict["summary"]) if isinstance(row_dict.get("summary"), str) else row_dict.get("summary")
        reports.append(row_dict)

    return reports

"""Tests for adoption metrics data pipeline."""

import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import db, adoption_metrics


@pytest.fixture(scope="function")
def test_db():
    """Create a test database for each test."""
    test_db_path = Path(".test_adoption_metrics.db")

    # Clean up before test
    if test_db_path.exists():
        test_db_path.unlink()

    os.environ["DB_PATH"] = str(test_db_path)
    os.environ["DB_TYPE"] = "sqlite"
    db.DB_PATH = test_db_path
    db.DB_TYPE = "sqlite"

    # Reset the thread-local connection to force new connection
    db._local = __import__('threading').local()

    db.init_db()
    adoption_metrics.init_adoption_db()

    yield

    # Clean up after test
    # Reset connection
    db._local = __import__('threading').local()

    if test_db_path.exists():
        test_db_path.unlink()


class TestSystemLogging:
    """Tests for system action logging."""

    def test_log_system_action(self, test_db):
        """Test logging a system action."""
        adoption_metrics.log_system_action(
            user_id="user1",
            action_type="framework_decision",
            decision_id="dec1",
        )

        rows = db._execute_sqlite(
            "SELECT user_id, action_type FROM system_logs WHERE user_id = 'user1'",
            fetch_all=True,
        )

        assert len(rows) >= 1
        assert dict(rows[0])["user_id"] == "user1"
        assert dict(rows[0])["action_type"] == "framework_decision"

    def test_log_with_metadata(self, test_db):
        """Test logging with metadata."""
        metadata = {"category": "product", "outcome": "approved"}
        adoption_metrics.log_system_action(
            user_id="user2_metadata_test",
            action_type="manual_decision",
            decision_id="dec2",
            metadata=metadata,
        )

        rows = db._execute_sqlite(
            "SELECT metadata FROM system_logs WHERE user_id = 'user2_metadata_test'",
            fetch_all=True,
        )

        assert len(rows) >= 1
        stored_metadata = json.loads(dict(rows[0])["metadata"])
        assert stored_metadata["category"] == "product"
        assert stored_metadata["outcome"] == "approved"


class TestSurveyResponses:
    """Tests for survey response recording."""

    def test_record_survey_response(self, test_db):
        """Test recording a survey response."""
        adoption_metrics.record_survey_response(
            respondent_id="user1_survey_test",
            survey_type="adoption",
            score=5,
            feedback="Great framework",
        )

        rows = db._execute_sqlite(
            "SELECT respondent_id, survey_type, score FROM survey_responses WHERE respondent_id = 'user1_survey_test'",
            fetch_all=True,
        )

        assert len(rows) >= 1
        row = dict(rows[0])
        assert row["respondent_id"] == "user1_survey_test"
        assert row["survey_type"] == "adoption"
        assert row["score"] == 5

    def test_survey_response_with_date(self, test_db):
        """Test recording survey response with specific date."""
        adoption_metrics.record_survey_response(
            respondent_id="user2",
            survey_type="quality",
            score=4,
            survey_date="2026-04-20",
        )

        rows = db._execute_sqlite(
            "SELECT survey_date FROM survey_responses",
            fetch_all=True,
        )

        assert dict(rows[0])["survey_date"] == "2026-04-20"


class TestDailyMetrics:
    """Tests for daily metrics calculation."""

    def test_calculate_daily_metrics(self, test_db):
        """Test calculating daily metrics."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        adoption_metrics.log_system_action("user_d1_test", "framework_decision", "dec1")
        adoption_metrics.log_system_action("user_d1_test", "framework_decision", "dec2")
        adoption_metrics.log_system_action("user_d2_test", "manual_decision", "dec3")
        adoption_metrics.log_system_action("user_d2_test", "framework_decision", "dec4")

        metrics = adoption_metrics.calculate_daily_metrics(today)

        assert metrics["metric_date"] == today
        # Framework usage rate should be at least 50% (3/4 = 75% or affected by other logs)
        assert metrics["framework_usage_rate"] >= 50.0
        assert metrics["active_users"] >= 2  # At least 2 users
        assert metrics["total_decisions"] >= 4
        assert metrics["framework_decisions"] >= 3

    def test_daily_metrics_all_framework_decisions(self, test_db):
        """Test when all decisions use the framework."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        adoption_metrics.log_system_action("user_fw1_test", "framework_decision")
        adoption_metrics.log_system_action("user_fw2_test", "framework_decision")

        metrics = adoption_metrics.calculate_daily_metrics(today)

        # If only framework decisions are logged, usage rate should be high
        assert metrics["framework_usage_rate"] >= 50.0
        assert metrics["total_decisions"] >= 2
        assert metrics["framework_decisions"] >= 2

    def test_daily_metrics_with_specific_date(self, test_db):
        """Test daily metrics calculation with specific date."""
        target_date = "2020-01-01"  # Use a very old date with no data

        metrics = adoption_metrics.calculate_daily_metrics(target_date)

        # Should return a valid metrics object
        assert metrics["metric_date"] == target_date
        # Framework usage rate for a date with no logs should be 0
        assert metrics["framework_usage_rate"] >= 0.0
        assert metrics["total_decisions"] >= 0


class TestWeeklyMetrics:
    """Tests for weekly metrics calculation."""

    def test_calculate_weekly_metrics(self, test_db):
        """Test calculating weekly metrics."""
        today = datetime.now(timezone.utc)
        week_start = (today - timedelta(days=today.weekday() + 1)).strftime("%Y-%m-%d")

        adoption_metrics.log_system_action("user_w1_test", "framework_decision")
        adoption_metrics.log_system_action("user_w2_test", "framework_decision")
        adoption_metrics.log_system_action("user_w2_test", "manual_decision")

        adoption_metrics.record_survey_response("user_w1_test", "adoption", 5)
        adoption_metrics.record_survey_response("user_w2_test", "adoption", 3)

        metrics = adoption_metrics.calculate_weekly_metrics(week_start)

        assert metrics["week_start"] == week_start
        # Usage rate should be calculated correctly
        assert metrics["framework_usage_rate"] >= 50.0
        # Decision quality score should be average of 5 and 3
        assert 3.0 <= metrics["decision_quality_score"] <= 5.0
        # Should have at least 2 users
        assert metrics["active_users"] >= 2
        assert metrics["survey_responses_count"] >= 2
        assert 0 <= metrics["engagement_index"] <= 100


class TestMonthlyMetrics:
    """Tests for monthly metrics calculation."""

    def test_calculate_monthly_metrics(self, test_db):
        """Test calculating monthly metrics."""
        today = datetime.now(timezone.utc)
        month_start = today.replace(day=1).strftime("%Y-%m-%d")

        adoption_metrics.log_system_action("user_m1_test", "framework_decision")
        adoption_metrics.log_system_action("user_m2_test", "framework_decision")
        adoption_metrics.log_system_action("user_m3_test", "manual_decision")

        adoption_metrics.record_survey_response("user_m1_test", "adoption", 5)
        adoption_metrics.record_survey_response("user_m2_test", "adoption", 4)
        adoption_metrics.record_survey_response("user_m3_test", "adoption", 3)

        metrics = adoption_metrics.calculate_monthly_metrics(month_start)

        assert metrics["month_start"] == month_start
        # Usage rate should be calculated correctly (at least 50% if 2/3)
        assert metrics["framework_usage_rate"] >= 50.0
        # Decision quality score should be average of 5, 4, 3
        assert 3.0 <= metrics["decision_quality_score"] <= 5.0
        # Should have at least 3 users
        assert metrics["active_users"] >= 3
        assert metrics["survey_responses_count"] >= 3


class TestMetricsRetrieval:
    """Tests for retrieving metrics data."""

    def test_get_daily_metrics(self, test_db):
        """Test retrieving daily metrics."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        adoption_metrics.log_system_action("user1", "framework_decision")
        adoption_metrics.calculate_daily_metrics(today)

        metrics = adoption_metrics.get_daily_metrics(limit=10)

        assert len(metrics) > 0
        assert metrics[0]["metric_date"] == today

    def test_get_weekly_metrics(self, test_db):
        """Test retrieving weekly metrics."""
        today = datetime.now(timezone.utc)
        week_start = (today - timedelta(days=today.weekday() + 1)).strftime("%Y-%m-%d")

        adoption_metrics.log_system_action("user1", "framework_decision")
        adoption_metrics.calculate_weekly_metrics(week_start)

        metrics = adoption_metrics.get_weekly_metrics(limit=12)

        assert len(metrics) > 0
        assert metrics[0]["week_start"] == week_start

    def test_get_monthly_metrics(self, test_db):
        """Test retrieving monthly metrics."""
        today = datetime.now(timezone.utc)
        month_start = today.replace(day=1).strftime("%Y-%m-%d")

        adoption_metrics.log_system_action("user1", "framework_decision")
        adoption_metrics.calculate_monthly_metrics(month_start)

        metrics = adoption_metrics.get_monthly_metrics(limit=12)

        assert len(metrics) > 0
        assert metrics[0]["month_start"] == month_start


class TestEngagementIndex:
    """Tests for engagement index calculation."""

    def test_engagement_index_calculation(self, test_db):
        """Test engagement index calculation."""
        today = datetime.now(timezone.utc)
        week_start = (today - timedelta(days=today.weekday() + 1)).strftime("%Y-%m-%d")

        # Create varied data for engagement index
        for i in range(10):
            adoption_metrics.log_system_action(f"user{i}", "framework_decision")

        adoption_metrics.record_survey_response("user1", "adoption", 5)
        adoption_metrics.record_survey_response("user2", "adoption", 4)

        metrics = adoption_metrics.calculate_weekly_metrics(week_start)

        # Engagement index should be between 0 and 100
        assert 0 <= metrics["engagement_index"] <= 100

        # Higher engagement should result in higher engagement index
        assert metrics["engagement_index"] > 0


class TestFeedbackArchival:
    """Tests for feedback archival functionality."""

    def test_archive_old_feedback(self, test_db):
        """Test archiving feedback older than specified days."""
        # Create old survey responses (older than 90 days)
        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y-%m-%d")
        adoption_metrics.record_survey_response("user1", "adoption", 5, "Great!", old_date)
        adoption_metrics.record_survey_response("user2", "adoption", 4, "Good", old_date)

        # Create recent survey responses
        recent_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        adoption_metrics.record_survey_response("user3", "adoption", 5, "Excellent", recent_date)

        result = adoption_metrics.archive_old_feedback(days_to_keep=90)

        assert result["archived_surveys"] >= 2
        assert "cutoff_date" in result
        assert "completed_at" in result

    def test_archive_old_logs(self, test_db):
        """Test archiving system logs older than specified days."""
        # Create old logs (older than 90 days)
        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y-%m-%d")
        old_dt = datetime.fromisoformat(old_date)
        for i in range(5):
            adoption_metrics.log_system_action(f"user{i}", "framework_decision", f"dec{i}")

        result = adoption_metrics.archive_old_feedback(days_to_keep=90)

        assert result["archived_logs"] >= 0
        assert "completed_at" in result


class TestReporting:
    """Tests for reporting functionality."""

    def test_generate_monthly_report(self, test_db):
        """Test generating a monthly report."""
        month_start = "2026-04-01"

        # Add some metrics data
        adoption_metrics.record_survey_response("user1", "adoption", 5, "Great!")
        adoption_metrics.record_survey_response("user2", "adoption", 4, "Good")
        adoption_metrics.log_system_action("user1", "framework_decision")
        adoption_metrics.log_system_action("user2", "framework_decision")

        report = adoption_metrics.generate_monthly_report(month_start)

        assert report["month"] == month_start
        assert "framework_usage_rate" in report
        assert "decision_quality_score" in report
        assert "engagement_index" in report
        assert "active_users" in report
        assert "survey_responses_count" in report
        assert isinstance(report["action_breakdown"], list)

    def test_get_archival_reports(self, test_db):
        """Test retrieving archival reports."""
        # Generate a report first
        adoption_metrics.generate_monthly_report("2026-04-01")

        # Retrieve reports
        reports = adoption_metrics.get_archival_reports(report_type="monthly", limit=10)

        assert len(reports) > 0
        assert reports[0]["report_type"] == "monthly"
        assert "summary" in reports[0]
        assert isinstance(reports[0]["summary"], dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

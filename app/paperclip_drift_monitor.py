"""Paperclip issue state drift detection and auto-recovery.

Monitors for blocked→in_progress drift and automatically restores issues to blocked state.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class DriftEvent:
    """Represents a detected state drift event."""

    def __init__(self, issue_id: str, issue_identifier: str, recovery_time: datetime, drift_count_1h: int):
        self.issue_id = issue_id
        self.issue_identifier = issue_identifier
        self.recovery_time = recovery_time
        self.drift_count_1h = drift_count_1h

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "issue_identifier": self.issue_identifier,
            "recovery_time": self.recovery_time.isoformat(),
            "drift_count_1h": self.drift_count_1h,
        }


class PaperclipDriftMonitor:
    """Monitor and recover from Paperclip issue state drift."""

    def __init__(self, api_url: str, api_key: str, company_id: str, run_id: str | None = None):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.company_id = company_id
        self.run_id = run_id or "local-drift-monitor"
        self.client = httpx.Client(timeout=30)
        self.drift_events: list[DriftEvent] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.client.close()

    def _api_request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """Make an authenticated API request to Paperclip."""
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_key}"
        headers["X-Paperclip-Run-Id"] = self.run_id

        url = f"{self.api_url}{path}"
        response = self.client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()

    def get_issues_with_blockers(self) -> list[dict[str, Any]]:
        """Fetch in-progress issues and enrich with blocker details from issue detail API."""
        issues_with_blockers = []
        try:
            result = self._api_request(
                "GET",
                f"/api/companies/{self.company_id}/issues",
                params={"status": "in_progress"},
            )
            for issue in result:
                issue_id = issue.get("id")
                if not issue_id:
                    continue
                detail = self._api_request("GET", f"/api/issues/{issue_id}")
                blockers = detail.get("blockedBy")
                if blockers:
                    issue["blockedBy"] = blockers
                    issues_with_blockers.append(issue)
        except Exception as e:
            logger.error(f"Failed to fetch issues: {e}")
        return issues_with_blockers

    def check_blocker_resolved(self, blocker: dict[str, Any] | str) -> bool:
        """Check if a blocker is resolved (done status)."""
        if isinstance(blocker, str):
            # Unknown status means unresolved until proven otherwise.
            return False
        return blocker.get("status") == "done"

    def detect_drift(self, issue: dict[str, Any]) -> bool:
        """Detect if an issue has drifted from blocked to in_progress."""
        # Drift detected if:
        # 1. Status is in_progress
        # 2. Has unresolved blockers (blockedBy is not empty and blockers aren't done)
        if issue.get("status") != "in_progress":
            return False

        blockers = issue.get("blockedBy", [])
        if not blockers:
            return False

        # Check if any blockers are still unresolved
        for blocker in blockers:
            if not self.check_blocker_resolved(blocker):
                return True

        return False

    def get_drift_count_1h(self, issue_id: str) -> int:
        """Get the number of drift events for this issue in the last hour."""
        # For now, return a simple count
        # In production, this would query a drift event log
        count = 0
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=1)
        for event in self.drift_events:
            if event.issue_id == issue_id and event.recovery_time >= cutoff_time:
                count += 1
        return count

    def restore_issue_to_blocked(self, issue_id: str, issue_identifier: str) -> bool:
        """Restore an issue from in_progress back to blocked state."""
        try:
            result = self._api_request(
                "PATCH",
                f"/api/issues/{issue_id}",
                json={
                    "status": "blocked",
                    "comment": f"[Auto-Recovery] blocked→in_progress 드리프트 감지 및 상태 복원\n\n"
                               f"Issue ID: {issue_identifier}\n"
                               f"Detection Time: {datetime.now(timezone.utc).isoformat()}\n"
                               f"Reason: blocked 상태 이슈가 blocker 미해결 상태에서 in_progress로 비정상 전이\n\n"
                               f"자동 복구 조치: status를 다시 blocked로 복원했습니다.",
                },
            )
            logger.info(f"Successfully restored {issue_identifier} to blocked state")
            return True
        except Exception as e:
            logger.error(f"Failed to restore {issue_identifier} to blocked: {e}")
            return False

    def monitor_and_recover(self) -> dict[str, Any]:
        """Run monitoring and auto-recovery process."""
        issues = self.get_issues_with_blockers()
        logger.info(f"Checking {len(issues)} issues with blockers for drift...")

        drifted_issues = []
        recovered_issues = []
        alarm_issues = []

        for issue in issues:
            if self.detect_drift(issue):
                drifted_issues.append(issue)
                issue_id = issue["id"]
                issue_identifier = issue.get("identifier", issue_id)

                # Get drift count in last hour
                drift_count = self.get_drift_count_1h(issue_id)
                logger.warning(f"Drift detected: {issue_identifier} (count={drift_count + 1})")

                # Restore to blocked
                if self.restore_issue_to_blocked(issue_id, issue_identifier):
                    recovered_issues.append(issue_identifier)

                    # Create drift event for logging
                    event = DriftEvent(
                        issue_id=issue_id,
                        issue_identifier=issue_identifier,
                        recovery_time=datetime.now(timezone.utc),
                        drift_count_1h=drift_count + 1,
                    )
                    self.drift_events.append(event)

                    # Check if alarm threshold exceeded
                    if drift_count + 1 >= 2:
                        alarm_issues.append(issue_identifier)
                        logger.critical(f"ALARM: {issue_identifier} has drifted {drift_count + 1} times in 1 hour!")

        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "issues_checked": len(issues),
            "issues_with_drift": len(drifted_issues),
            "issues_recovered": len(recovered_issues),
            "alarm_triggered": len(alarm_issues) > 0,
            "recovered_identifiers": recovered_issues,
            "alarm_issues": alarm_issues,
            "events": [event.to_dict() for event in self.drift_events],
        }

        logger.info(json.dumps(summary, indent=2))
        return summary


async def run_drift_monitor_async(api_url: str, api_key: str, company_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Run drift monitor (async wrapper for compatibility)."""
    with PaperclipDriftMonitor(api_url, api_key, company_id, run_id) as monitor:
        return monitor.monitor_and_recover()


def run_drift_monitor(
    api_url: str | None = None,
    api_key: str | None = None,
    company_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run drift monitor with environment variables."""
    # Only use environment fallback when the caller omits a value (`None`).
    # Passing an explicit empty string should be treated as invalid config.
    if api_url is None:
        api_url = os.environ.get("PAPERCLIP_API_URL", "http://localhost:8000")
    if api_key is None:
        api_key = os.environ.get("PAPERCLIP_API_KEY", "")
    if company_id is None:
        company_id = os.environ.get("PAPERCLIP_COMPANY_ID", "")
    if run_id is None:
        run_id = os.environ.get("PAPERCLIP_RUN_ID", "")

    missing = []
    if not api_url:
        missing.append("PAPERCLIP_API_URL")
    if not api_key:
        missing.append("PAPERCLIP_API_KEY")
    if not company_id:
        missing.append("PAPERCLIP_COMPANY_ID")
    if missing:
        raise ValueError(f"Missing required configuration: {', '.join(missing)}")

    with PaperclipDriftMonitor(api_url, api_key, company_id, run_id) as monitor:
        return monitor.monitor_and_recover()

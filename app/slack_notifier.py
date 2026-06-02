"""Slack alert delivery helpers for operations monitoring."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any

import httpx


def _alert_id(check: dict[str, Any], severity: str, code: str, message: str) -> str:
    source = "|".join(
        [
            str(check.get("collector") or check.get("name") or "unknown"),
            severity,
            code,
            message,
            datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        ]
    )
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def create_alert_from_check(check: dict[str, Any], severity: str, code: str, message: str) -> str:
    """Create an alert id and deliver it to Slack when a webhook is configured."""
    alert_id = _alert_id(check, severity, code, message)
    webhook_url = os.environ.get("ALERT_SLACK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return alert_id

    payload = {
        "text": f"[{severity.upper()}] {message}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{severity.upper()}* `{code}`\n{message}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"collector={check.get('collector', 'unknown')} "
                            f"alert_id={alert_id}"
                        ),
                    }
                ],
            },
        ],
    }
    try:
        httpx.post(webhook_url, json=payload, timeout=5)
    except Exception:
        pass
    return alert_id

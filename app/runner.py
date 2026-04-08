"""Shared helper to run a collector and persist its job lifecycle.

Used by both the REST `/collect` endpoint and the MCP server tools so the
job-creation / error-handling logic lives in exactly one place.
"""

import uuid
from typing import Any, Awaitable, Callable

from app import db

CollectorFn = Callable[..., Awaitable[int]]


async def run_collector(
    name: str,
    fn: CollectorFn,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Create a job, run the collector, finalize, and return the job dict."""
    job_id = uuid.uuid4().hex[:12]
    db.create_job(job_id, name)
    try:
        count = await fn(job_id, symbols)
        db.finish_job(job_id, count)
    except Exception as exc:  # noqa: BLE001 — we want to record any failure
        db.finish_job(job_id, 0, str(exc))
    job = db.get_job(job_id)
    return job or {"id": job_id, "collector": name, "status": "unknown"}

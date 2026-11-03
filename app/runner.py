"""Shared helper to run a collector and persist its job lifecycle.

Used by both the REST `/collect` endpoint and the MCP server tools so the
job-creation / error-handling logic lives in exactly one place.
"""

import asyncio
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
    await asyncio.to_thread(db.create_job, job_id, name)
    return await run_existing_collector(job_id, name, fn, symbols)


async def run_existing_collector(
    job_id: str,
    name: str,
    fn: CollectorFn,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Keep synchronous collector DB calls off the API event loop."""
    return await asyncio.to_thread(_run_in_worker, job_id, name, fn, symbols)


def _run_in_worker(
    job_id: str, name: str, fn: CollectorFn, symbols: list[str] | None
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        try:
            resolved_symbols = db.resolve_collection_symbols(name, symbols)
            count = await fn(job_id, resolved_symbols)
            db.finish_job(job_id, count)
        except Exception as exc:  # noqa: BLE001 — preserve failed job evidence
            db.finish_job(job_id, 0, str(exc))
        job = db.get_job(job_id)
        return job or {"id": job_id, "collector": name, "status": "unknown"}

    # Each collector creates its HTTP clients/async locks inside this worker loop.
    return asyncio.run(run())

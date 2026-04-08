"""Background scheduler that runs all collectors on a fixed interval.

Default cadence is once per hour for every registered collector. The first
run happens shortly after startup so the database has fresh data without
having to wait an entire interval. Each collector loop is isolated so a
failure in one source does not affect the others.
"""

import asyncio
import logging
import os
import random
from typing import Awaitable, Callable

from app.runner import run_collector

log = logging.getLogger(__name__)

CollectorFn = Callable[..., Awaitable[int]]

DEFAULT_INTERVAL_SEC = 3600  # 1 hour


def _interval_for(name: str) -> int:
    """Resolve the interval for a collector from env vars, falling back to default."""
    per_collector = os.environ.get(f"SCHED_{name.upper()}_INTERVAL")
    if per_collector is not None:
        try:
            return int(per_collector)
        except ValueError:
            log.warning("Invalid SCHED_%s_INTERVAL=%r", name.upper(), per_collector)

    global_override = os.environ.get("SCHED_INTERVAL_SEC")
    if global_override is not None:
        try:
            return int(global_override)
        except ValueError:
            log.warning("Invalid SCHED_INTERVAL_SEC=%r", global_override)

    return DEFAULT_INTERVAL_SEC


class Scheduler:
    """Owns one asyncio task per collector."""

    def __init__(self, collectors: dict[str, CollectorFn]):
        self._collectors = collectors
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if os.environ.get("SCHED_DISABLED") == "1":
            log.info("Scheduler disabled via SCHED_DISABLED=1")
            return
        for name, fn in self._collectors.items():
            interval = _interval_for(name)
            if interval <= 0:
                log.info("Scheduler skipping %s (interval=%s)", name, interval)
                continue
            task = asyncio.create_task(
                self._loop(name, fn, interval), name=f"sched-{name}"
            )
            self._tasks.append(task)
            log.info("Scheduler started %s every %ss", name, interval)

    async def stop(self) -> None:
        self._stopping.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    async def _loop(self, name: str, fn: CollectorFn, interval: int) -> None:
        # Small jitter so multiple collectors don't all hit external APIs at once.
        await asyncio.sleep(random.uniform(0, min(5, interval)))
        while not self._stopping.is_set():
            try:
                await run_collector(name, fn)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — runner already records errors
                log.exception("Scheduler iteration for %s failed: %s", name, exc)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

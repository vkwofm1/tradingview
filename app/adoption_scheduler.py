"""Scheduler for adoption metrics calculation and rollups."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import adoption_metrics

log = logging.getLogger(__name__)


class AdoptionMetricsScheduler:
    """Schedules daily, weekly, and monthly adoption metrics calculation."""

    def __init__(self):
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        """Start scheduled metric calculations."""
        log.info("Starting adoption metrics scheduler")
        task = asyncio.create_task(self._daily_rollup_loop(), name="adoption-daily-rollup")
        self._tasks.append(task)
        task = asyncio.create_task(self._weekly_rollup_loop(), name="adoption-weekly-rollup")
        self._tasks.append(task)
        task = asyncio.create_task(self._monthly_rollup_loop(), name="adoption-monthly-rollup")
        self._tasks.append(task)
        task = asyncio.create_task(self._archival_loop(), name="adoption-archival")
        self._tasks.append(task)

    async def stop(self) -> None:
        """Stop all scheduled tasks."""
        log.info("Stopping adoption metrics scheduler")
        self._stopping.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    async def _daily_rollup_loop(self) -> None:
        """Run daily metrics calculation at midnight UTC."""
        while not self._stopping.is_set():
            try:
                now = datetime.now(timezone.utc)
                tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
                if now.hour == 0 and now.minute < 5:
                    # Run at midnight
                    yesterday = (now.replace(hour=0, minute=0, second=0, microsecond=0) -
                               timedelta(days=1))
                    result = adoption_metrics.calculate_daily_metrics(
                        target_date=yesterday.strftime("%Y-%m-%d")
                    )
                    log.info(f"Daily metrics calculated: {result}")

                await asyncio.sleep(300)  # Check every 5 minutes
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception(f"Daily rollup failed: {exc}")
                await asyncio.sleep(300)

    async def _weekly_rollup_loop(self) -> None:
        """Run weekly metrics calculation every Monday at midnight UTC."""
        while not self._stopping.is_set():
            try:
                now = datetime.now(timezone.utc)
                if now.weekday() == 0 and now.hour == 0 and now.minute < 5:
                    # Run on Monday midnight
                    previous_monday = (now.replace(hour=0, minute=0, second=0, microsecond=0) -
                                     timedelta(days=7))
                    result = adoption_metrics.calculate_weekly_metrics(
                        week_start=previous_monday.strftime("%Y-%m-%d")
                    )
                    log.info(f"Weekly metrics calculated: {result}")

                await asyncio.sleep(3600)  # Check every hour
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception(f"Weekly rollup failed: {exc}")
                await asyncio.sleep(3600)

    async def _monthly_rollup_loop(self) -> None:
        """Run monthly metrics calculation on the 1st of each month at midnight UTC."""
        while not self._stopping.is_set():
            try:
                now = datetime.now(timezone.utc)
                if now.day == 1 and now.hour == 0 and now.minute < 5:
                    # Run on the 1st of the month
                    previous_month_start = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) -
                                           timedelta(days=1)).replace(day=1)
                    result = adoption_metrics.calculate_monthly_metrics(
                        month_start=previous_month_start.strftime("%Y-%m-%d")
                    )
                    log.info(f"Monthly metrics calculated: {result}")

                await asyncio.sleep(3600)  # Check every hour
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception(f"Monthly rollup failed: {exc}")
                await asyncio.sleep(3600)

    async def _archival_loop(self) -> None:
        """Run feedback archival on the 15th of each month at midnight UTC."""
        while not self._stopping.is_set():
            try:
                now = datetime.now(timezone.utc)
                if now.day == 15 and now.hour == 0 and now.minute < 5:
                    # Run on the 15th of the month
                    result = adoption_metrics.archive_old_feedback(days_to_keep=90)
                    log.info(f"Feedback archival completed: {result}")
                    # Generate report for previous month
                    if now.month == 1:
                        report_month = now.replace(year=now.year - 1, month=12, day=1)
                    else:
                        report_month = now.replace(month=now.month - 1, day=1)
                    report = adoption_metrics.generate_monthly_report(report_month.strftime("%Y-%m-%d"))
                    log.info(f"Monthly report generated: {report}")

                await asyncio.sleep(3600)  # Check every hour
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception(f"Archival loop failed: {exc}")
                await asyncio.sleep(3600)

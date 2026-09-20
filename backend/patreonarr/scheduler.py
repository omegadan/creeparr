"""Periodic tasks (APScheduler) with a small registry for the UI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select

from patreonarr.core.errors import Conflict
from patreonarr.db.engine import session_scope
from patreonarr.db.enums import ScanMode
from patreonarr.db.models import Creator, History

if TYPE_CHECKING:
    from patreonarr.services import Services

log = logging.getLogger(__name__)


@dataclass
class TaskInfo:
    name: str
    description: str
    interval_seconds: int | None
    fn: Callable[[], Awaitable[Any]]
    last_run_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    running: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class SchedulerService:
    def __init__(self, services: Services) -> None:
        self.services = services
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.tasks: dict[str, TaskInfo] = {}

    # ---- registry ------------------------------------------------------------------

    def _register(
        self, name: str, description: str, seconds: int | None, fn: Callable[[], Awaitable[Any]]
    ) -> None:
        self.tasks[name] = TaskInfo(name, description, seconds, fn)
        if seconds:
            self.scheduler.add_job(
                self._wrap(name),
                IntervalTrigger(seconds=seconds),
                id=name,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                next_run_time=datetime.now(UTC) + timedelta(seconds=min(seconds, 60)),
            )
        elif self.scheduler.get_job(name):
            self.scheduler.remove_job(name)

    def _wrap(self, name: str) -> Callable[[], Awaitable[None]]:
        async def _run() -> None:
            await self.run_task(name)

        return _run

    async def run_task(self, name: str) -> Any:
        info = self.tasks[name]
        if info.running:
            return None
        info.running = True
        info.last_run_at = datetime.now(UTC)
        try:
            result = await info.fn()
            info.last_status = "ok"
            info.last_error = None
            return result
        except Conflict as exc:
            info.last_status = "skipped"
            info.last_error = exc.message
            log.info("task %s skipped: %s", name, exc.message)
        except Exception as exc:  # noqa: BLE001
            info.last_status = "error"
            info.last_error = str(exc)
            log.exception("task %s failed", name)
        finally:
            info.running = False
        return None

    def list_tasks(self) -> list[dict[str, Any]]:
        out = []
        for info in self.tasks.values():
            job = self.scheduler.get_job(info.name)
            out.append(
                {
                    "name": info.name,
                    "description": info.description,
                    "interval_seconds": info.interval_seconds,
                    "next_run_at": job.next_run_time.isoformat()
                    if job and job.next_run_time
                    else None,
                    "last_run_at": info.last_run_at.isoformat() if info.last_run_at else None,
                    "last_status": info.last_status,
                    "last_error": info.last_error,
                    "running": info.running,
                }
            )
        return out

    def next_scan_at(self) -> datetime | None:
        job = self.scheduler.get_job("scan_monitored")
        return job.next_run_time if job else None

    # ---- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        s = self.services.settings.get()
        self._register(
            "scan_monitored",
            "Scan monitored creators for new posts",
            s.scan.interval_minutes * 60 if s.scan.interval_minutes else None,
            self._scan_monitored,
        )
        self._register(
            "full_rescan",
            "Full re-scan of creators whose last full scan is older than the configured age",
            24 * 3600,
            self._full_rescan,
        )
        self._register(
            "requeue_failed",
            "Re-queue failed downloads whose retry time has come",
            60,
            self._requeue_failed,
        )
        self._register(
            "session_check",
            "Verify the Patreon session is still valid",
            24 * 3600,
            self._session_check,
        )
        self._register("prune", "Delete old history and job rows", 24 * 3600, self._prune)
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def apply_settings(self) -> None:
        s = self.services.settings.get()
        seconds = s.scan.interval_minutes * 60 if s.scan.interval_minutes else None
        info = self.tasks.get("scan_monitored")
        if info is None or info.interval_seconds == seconds:
            return
        self._register("scan_monitored", info.description, seconds, info.fn)

    # ---- tasks ---------------------------------------------------------------------

    async def _scan_monitored(self) -> dict[str, int]:
        n = self.services.scan_manager.request_scan_all(ScanMode.AUTO, trigger="schedule")
        return {"queued": n}

    async def _full_rescan(self) -> dict[str, int]:
        days = self.services.settings.get().scan.full_rescan_days
        if not days:
            return {"queued": 0}
        cutoff = datetime.now(UTC) - timedelta(days=days)
        with session_scope(self.services.session_factory) as s:
            ids = (
                s.execute(
                    select(Creator.id).where(
                        Creator.monitored.is_(True),
                        (Creator.last_full_scan_at.is_(None))
                        | (Creator.last_full_scan_at < cutoff),
                    )
                )
                .scalars()
                .all()
            )
        n = 0
        for cid in ids:
            if self.services.scan_manager.request_scan(cid, ScanMode.FULL, trigger="schedule"):
                n += 1
        return {"queued": n}

    async def _requeue_failed(self) -> dict[str, int]:
        n = await asyncio.to_thread(self.services.downloads.requeue_due_retries)
        return {"requeued": n}

    async def _session_check(self) -> dict[str, bool]:
        ok = await self.services.patreon.check_session()
        return {"ok": ok}

    async def _prune(self) -> dict[str, int]:
        h = self.services.settings.get().history
        cutoff = datetime.now(UTC) - timedelta(days=h.retention_days)

        def _do() -> int:
            with session_scope(self.services.session_factory) as s:
                res = s.execute(delete(History).where(History.occurred_at < cutoff))
                return res.rowcount or 0

        removed_history = await asyncio.to_thread(_do)
        removed_jobs = await asyncio.to_thread(
            self.services.downloads.prune_jobs, h.job_retention_days
        )
        return {"history": removed_history, "jobs": removed_jobs}

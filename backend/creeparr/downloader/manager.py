"""The download workers: each claims a queued download, runs it and hands its outcome to the
media lifecycle. Claiming, the run and the lifecycle hold the rules; this holds the workers,
pause and resume, cancelling, and the free-space check."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from creeparr.config import EnvConfig
from creeparr.core.downloads import INTERNAL_ERROR, Claim, Outcome
from creeparr.core.events import EventBus
from creeparr.core.history import record_event
from creeparr.core.lifecycle import MediaLifecycle
from creeparr.core.server_events import QueuePausedEvent, QueueResumedEvent
from creeparr.core.settings_service import SettingsService
from creeparr.core.state import get_state, set_state
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import EventType
from creeparr.downloader.claims import Claims
from creeparr.downloader.fs import free_space_bytes
from creeparr.downloader.handlers.base import ProgressReporter
from creeparr.downloader.progress import JobProgress
from creeparr.downloader.run import DownloadRun

log = logging.getLogger(__name__)

PAUSED_KEY = "downloads_paused"


class DownloadManager:
    def __init__(
        self,
        env: EnvConfig,
        session_factory: SessionFactory,
        settings: SettingsService,
        bus: EventBus,
        lifecycle: MediaLifecycle,
        claims: Claims,
        run: DownloadRun,
        progress: JobProgress,
        *,
        free_space: Callable[[Path], int] = free_space_bytes,
    ) -> None:
        self.env = env
        self._factory = session_factory
        self.settings = settings
        self.bus = bus
        self._lifecycle = lifecycle
        self._claims = claims
        self._run = run
        self._progress = progress
        self._free_space = free_space
        self._workers: list[asyncio.Task[None]] = []
        self._running: dict[int, ProgressReporter] = {}
        self._kick = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self.paused_reason: str | None = None
        self._last_disk_check = 0.0
        self._disk_ok = True

    # ---- lifecycle -----------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        await asyncio.to_thread(self._lifecycle.recover_interrupted)
        await asyncio.to_thread(self._run.sweep)
        with session_scope(self._factory) as s:
            self.paused_reason = "user" if get_state(s, PAUSED_KEY, False) else None
        self._resize_workers()

    async def stop(self) -> None:
        self._stop.set()
        for reporter in self._running.values():
            reporter.cancel_event.set()
        self._kick.set()
        for t in self._workers:
            t.cancel()
        for t in self._workers:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._workers.clear()

    def _resize_workers(self) -> None:
        want = self.settings.get().downloads.concurrency
        alive = [t for t in self._workers if not t.done()]
        while len(alive) < want:
            idx = len(alive)
            alive.append(asyncio.create_task(self._worker(idx), name=f"download-worker-{idx}"))
        self._workers = alive  # extra workers exit on their own when they see they're surplus

    def apply_settings(self) -> None:
        self._resize_workers()
        self._claims.settings_changed()
        self.notify()

    def notify(self) -> None:
        """Wake idle workers. Safe from any thread: API requests and scans run in their own."""
        loop = self._loop
        if loop is None or loop.is_closed():
            self._kick.set()
            return
        try:
            on_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False
        if on_loop:
            self._kick.set()
        else:
            loop.call_soon_threadsafe(self._kick.set)

    # ---- pause / resume / cancel ---------------------------------------------------

    @property
    def paused(self) -> bool:
        return self.paused_reason is not None

    def pause(self, reason: str = "user") -> None:
        self.paused_reason = reason
        if reason == "user":
            with session_scope(self._factory) as s:
                set_state(s, PAUSED_KEY, True)
                record_event(s, self.bus, EventType.QUEUE_PAUSED, "Download queue paused")
        self.bus.publish(QueuePausedEvent(reason=reason))

    def resume(self) -> None:
        self.paused_reason = None
        with session_scope(self._factory) as s:
            set_state(s, PAUSED_KEY, False)
        self.bus.publish(QueueResumedEvent())
        self.notify()

    def cancel_job(self, job_id: int) -> bool:
        reporter = self._running.get(job_id)
        if reporter is None:
            return False
        reporter.cancel_event.set()
        return True

    def status(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "paused_reason": self.paused_reason,
            "workers": len([t for t in self._workers if not t.done()]),
            "running_jobs": list(self._running.keys()),
            "free_bytes": min(
                (self._free_space(r) for r in self.env.download_roots().values()), default=0
            ),
        }

    def provider_status(self) -> list[dict[str, Any]]:
        return self._claims.provider_status(paused=self.paused)

    # ---- workers -------------------------------------------------------------------

    def _check_disk(self) -> bool:
        now = time.monotonic()
        if now - self._last_disk_check < 30:
            return self._disk_ok
        self._last_disk_check = now
        min_free = self.settings.get().downloads.min_free_mb * 1024 * 1024
        roots = self.env.download_roots().values()
        free = min((self._free_space(r) for r in roots), default=0)
        ok = free >= min_free
        if not ok and self.paused_reason is None:
            log.error("free space %.1f MB below minimum; pausing downloads", free / 1e6)
            self.pause("disk_full")
        elif ok and self.paused_reason == "disk_full":
            log.info("free space recovered; resuming downloads")
            self.resume()
        self._disk_ok = ok
        return ok

    async def _worker(self, idx: int) -> None:
        worker_id = f"w{idx}"
        while not self._stop.is_set():
            if idx >= self.settings.get().downloads.concurrency:
                return  # surplus after a concurrency decrease
            # Nothing may escape this loop: a dead worker is never restarted, so the pool
            # would silently shrink until downloads stop altogether.
            try:
                await self._worker_step(worker_id)
            except Exception:  # noqa: BLE001
                log.exception("download worker %s hit an unexpected error", worker_id)
                await self._sleep(5)

    async def _worker_step(self, worker_id: str) -> None:
        self._check_disk()
        if self.paused:
            await self._sleep(5)
            return
        claim = await asyncio.to_thread(self._claims.next, worker_id)
        if claim is None:
            await self._sleep(5)
            return
        reporter = self._progress.reporter(claim.job_id)
        self._running[claim.job_id] = reporter
        try:
            outcome = await self._run.run(claim, reporter)
            await asyncio.to_thread(self._record, claim, outcome)
        finally:
            self._running.pop(claim.job_id, None)
            self._kick.set()

    def _record(self, claim: Claim, outcome: Outcome) -> None:
        try:
            with session_scope(self._factory) as s:
                self._lifecycle.record_outcome(s, claim, outcome)
        except Exception:  # noqa: BLE001
            # Leave the media retryable rather than stuck as downloading.
            log.exception("job %s: could not record its outcome %r", claim.job_id, outcome)
            with session_scope(self._factory) as s:
                self._lifecycle.record_outcome(s, claim, INTERNAL_ERROR)

    async def _sleep(self, seconds: float) -> None:
        self._kick.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._kick.wait(), timeout=seconds)

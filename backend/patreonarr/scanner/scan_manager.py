"""Serialises scans behind a single worker so the Patreon rate limit is respected."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from patreonarr.core.errors import Conflict
from patreonarr.core.events import EventBus
from patreonarr.core.patreon_service import PatreonService
from patreonarr.db.engine import SessionFactory, session_scope
from patreonarr.db.enums import AuthState, ScanMode
from patreonarr.db.models import Creator
from patreonarr.patreon.errors import AuthError, CloudflareChallengeError
from patreonarr.scanner.scanner import Scanner

log = logging.getLogger(__name__)


@dataclass
class ScanRequest:
    creator_id: int
    mode: ScanMode
    trigger: str
    requested_at: datetime


class ScanManager:
    def __init__(
        self,
        scanner: Scanner,
        session_factory: SessionFactory,
        patreon: PatreonService,
        bus: EventBus,
    ) -> None:
        self.scanner = scanner
        self._factory = session_factory
        self.patreon = patreon
        self.bus = bus
        self._queue: asyncio.Queue[ScanRequest] = asyncio.Queue()
        self._pending: dict[int, ScanRequest] = {}
        self._current: ScanRequest | None = None
        self._cancel = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stop = False

    async def start(self) -> None:
        self._stop = False
        self._task = asyncio.create_task(self._worker(), name="scan-manager")

    async def stop(self) -> None:
        self._stop = True
        self._cancel.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    # ---- API -----------------------------------------------------------------------

    def request_scan(
        self, creator_id: int, mode: ScanMode = ScanMode.AUTO, trigger: str = "manual"
    ) -> bool:
        """Enqueue a scan; returns False if one is already pending/running for this creator."""
        state = self.patreon.get_auth_status().get("state")
        if state in (AuthState.INVALID, AuthState.CHALLENGE, AuthState.UNCONFIGURED):
            raise Conflict(
                "Patreon session is not valid; fix it in Settings before scanning",
                code="auth_invalid",
            )
        if creator_id in self._pending:
            existing = self._pending[creator_id]
            if mode == ScanMode.FULL and existing.mode != ScanMode.FULL:
                existing.mode = ScanMode.FULL
            return False
        if self._current is not None and self._current.creator_id == creator_id:
            return False
        req = ScanRequest(creator_id, mode, trigger, datetime.now(UTC))
        self._pending[creator_id] = req
        self._queue.put_nowait(req)
        self.bus.publish("scan.queued", {"creator_id": creator_id, "mode": mode})
        return True

    def request_scan_all(self, mode: ScanMode = ScanMode.AUTO, trigger: str = "manual") -> int:
        with session_scope(self._factory) as s:
            ids = s.execute(select(Creator.id).where(Creator.monitored.is_(True))).scalars().all()
        n = 0
        for cid in ids:
            try:
                if self.request_scan(cid, mode, trigger):
                    n += 1
            except Conflict:
                break
        return n

    def cancel_current(self) -> bool:
        if self._current is None:
            return False
        self._cancel.set()
        return True

    def status(self) -> dict[str, Any]:
        return {
            "running": (
                {"creator_id": self._current.creator_id, "mode": self._current.mode}
                if self._current
                else None
            ),
            "pending": [
                {"creator_id": r.creator_id, "mode": r.mode, "trigger": r.trigger}
                for r in self._pending.values()
            ],
        }

    def is_busy(self, creator_id: int) -> bool:
        return creator_id in self._pending or (
            self._current is not None and self._current.creator_id == creator_id
        )

    # ---- worker --------------------------------------------------------------------

    async def _worker(self) -> None:
        while not self._stop:
            req = await self._queue.get()
            self._pending.pop(req.creator_id, None)
            self._current = req
            self._cancel.clear()
            try:
                await self.scanner.scan_creator(req.creator_id, req.mode, req.trigger, self._cancel)
            except (AuthError, CloudflareChallengeError):
                log.error("auth failure during scan; dropping %d pending scans", len(self._pending))
                self._drain()
            except Exception:  # noqa: BLE001
                log.exception("scan worker error for creator %s", req.creator_id)
            finally:
                self._current = None
                self._queue.task_done()

    def _drain(self) -> None:
        self._pending.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

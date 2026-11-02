"""Serialises scans behind a single worker so the Patreon rate limit is respected.

Scan admission is asked twice: when a scan is queued and when the worker starts it, so a scan
queued before its creator or provider was switched off, or its session expired, is skipped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from creeparr.core.errors import Conflict, NotFound
from creeparr.core.events import EventBus
from creeparr.core.server_events import ScanQueuedEvent, ScanSkippedEvent
from creeparr.db.engine import SessionFactory
from creeparr.db.enums import ScanMode, ScanTrigger
from creeparr.providers.errors import AuthError, CloudflareChallengeError
from creeparr.providers.registry import ProviderRegistry
from creeparr.scanner.admission import Refusal, ScanAdmission
from creeparr.scanner.scanner import Scanner

log = logging.getLogger(__name__)


@dataclass
class ScanRequest:
    creator_id: int
    mode: ScanMode
    trigger: ScanTrigger
    requested_at: datetime


class ScanManager:
    def __init__(
        self,
        scanner: Scanner,
        session_factory: SessionFactory,
        providers: ProviderRegistry,
        bus: EventBus,
    ) -> None:
        self.scanner = scanner
        self.bus = bus
        self.admission = ScanAdmission(session_factory, providers)
        self._queue: asyncio.Queue[ScanRequest] = asyncio.Queue()
        self._pending: dict[int, ScanRequest] = {}
        self._current: ScanRequest | None = None
        self._cancel = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stop = False

    async def start(self) -> None:
        self._stop = False
        await asyncio.to_thread(self.scanner.close_interrupted_runs)
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
        self,
        creator_id: int,
        mode: ScanMode = ScanMode.AUTO,
        trigger: ScanTrigger = ScanTrigger.MANUAL,
    ) -> bool:
        """Enqueue a scan; returns False if one is already pending/running for this creator.

        Raises NotFound for a creator that doesn't exist and Conflict if admission refuses.
        """
        refusal = self.admission.check(creator_id, trigger)
        if refusal is not None:
            raise refusal.as_conflict()
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
        self.bus.publish(ScanQueuedEvent(creator_id=creator_id, mode=mode))
        return True

    def request_scan_all(
        self, mode: ScanMode = ScanMode.AUTO, trigger: ScanTrigger = ScanTrigger.SCAN_ALL
    ) -> int:
        return self.request_scans(
            [c.creator_id for c in self.admission.candidates(trigger)], mode, trigger
        )

    def request_scans(self, creator_ids: list[int], mode: ScanMode, trigger: ScanTrigger) -> int:
        """Queue scans of many creators, skipping any that can't scan right now.

        A creator refused or deleted since its id was read only skips that creator, never
        the rest. Returns how many were queued.
        """
        n = 0
        for cid in creator_ids:
            try:
                if self.request_scan(cid, mode, trigger):
                    n += 1
            except (Conflict, NotFound) as exc:
                log.debug("%s scan of creator %s skipped: %s", trigger, cid, exc)
        return n

    def scan_new_creator(self, creator_id: int) -> bool:
        """Queue the first, full scan of a creator just added. A refusal is reported (logged
        and sent to the UI) rather than raised: the creator was added either way."""
        try:
            return self.request_scan(creator_id, ScanMode.FULL, ScanTrigger.ADD)
        except Conflict:
            refusal = self.admission.check(creator_id, ScanTrigger.ADD)
            if refusal is not None:
                self._report_skipped(refusal)
            return False

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
            try:
                if not await asyncio.to_thread(self._still_admitted, req):
                    continue
                self._current = req
                self._cancel.clear()
                await self.scanner.scan_creator(req.creator_id, req.mode, req.trigger, self._cancel)
            except (AuthError, CloudflareChallengeError):
                # The scan marked the provider's session invalid, so admission refuses its
                # other queued scans when the worker reaches them.
                log.error("auth failure while scanning creator %s", req.creator_id)
            except Exception:  # noqa: BLE001
                log.exception("scan worker error for creator %s", req.creator_id)
            finally:
                self._current = None
                self._queue.task_done()

    def _still_admitted(self, req: ScanRequest) -> bool:
        try:
            refusal = self.admission.check(req.creator_id, req.trigger)
        except NotFound:
            log.info("queued scan of creator %s dropped: creator was deleted", req.creator_id)
            return False
        if refusal is not None:
            self._report_skipped(refusal)
            return False
        return True

    def _report_skipped(self, refusal: Refusal) -> None:
        log.info("scan of %s skipped: %s", refusal.creator_name, refusal.message)
        self.bus.publish(
            ScanSkippedEvent(
                creator_id=refusal.creator_id,
                creator_name=refusal.creator_name,
                code=refusal.code,
                message=refusal.message,
            )
        )

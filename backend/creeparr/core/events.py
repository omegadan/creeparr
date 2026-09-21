"""Tiny in-process pub/sub used for SSE and cross-component notifications."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, max_queue: int = 1000) -> None:
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._max_queue = max_queue

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        msg = {"event": event, "data": data or {}, "ts": datetime.now(UTC).isoformat()}
        with self._lock:
            subs = list(self._subs)
        if not subs:
            return

        def _deliver() -> None:
            for q in subs:
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    log.debug("event subscriber queue full; dropping %s", event)

        loop = self._loop
        if loop is None or loop.is_closed():
            _deliver()
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _deliver()
        else:
            loop.call_soon_threadsafe(_deliver)

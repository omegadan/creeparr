"""Outbound notifications (webhook / Discord / ntfy) driven by history events."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx

from patrearr.core.events import EventBus
from patrearr.core.settings_service import SettingsService

log = logging.getLogger(__name__)

# History event types that can raise a notification, mapped to the settings toggle.
NOTIFY_EVENTS = {
    "auth_invalid": "notify_auth_invalid",
    "scan_failed": "notify_scan_failed",
    "download_failed": "notify_download_failed",
    "scan_completed": "notify_scan_completed",
    "media_unsupported": "notify_download_failed",
}


class NotificationService:
    def __init__(self, settings: SettingsService, bus: EventBus) -> None:
        self.settings = settings
        self.bus = bus
        self._task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[dict[str, Any]] = self.bus.subscribe()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="notifications")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self.bus.unsubscribe(self._queue)

    async def _run(self) -> None:
        while True:
            msg = await self._queue.get()
            if msg.get("event") != "history.added":
                continue
            data = msg.get("data") or {}
            event_type = data.get("event_type")
            toggle = NOTIFY_EVENTS.get(event_type)
            if not toggle:
                continue
            n = self.settings.get().notifications
            if not getattr(n, toggle, False):
                continue
            title = event_type.replace("_", " ").title()
            body = data.get("message") or title
            level = data.get("level", "info")
            with contextlib.suppress(Exception):
                await self._dispatch(title, body, level)

    async def _dispatch(self, title: str, body: str, level: str) -> None:
        n = self.settings.get().notifications
        async with httpx.AsyncClient(timeout=15) as client:
            if n.discord_webhook:
                emoji = {"error": "🔴", "warning": "🟠"}.get(level, "🔵")
                content = f"{emoji} **{title}** — {body}"
                await self._safe(client.post(n.discord_webhook, json={"content": content}))
            if n.ntfy_url:
                headers = {
                    "Title": f"Patrearr: {title}",
                    "Priority": "high" if level == "error" else "default",
                }
                await self._safe(client.post(n.ntfy_url, content=body.encode(), headers=headers))
            if n.webhook_url:
                payload = {"app": "patrearr", "title": title, "message": body, "level": level}
                await self._safe(client.post(n.webhook_url, json=payload))

    @staticmethod
    async def _safe(coro) -> None:  # noqa: ANN001
        try:
            resp = await coro
            if resp.status_code >= 400:
                log.warning("notification target returned HTTP %s", resp.status_code)
        except Exception as exc:  # noqa: BLE001
            log.warning("notification failed: %s", exc)

    async def test(self) -> dict[str, Any]:
        n = self.settings.get().notifications
        pairs = (("discord", n.discord_webhook), ("ntfy", n.ntfy_url), ("webhook", n.webhook_url))
        targets = [t for t, v in pairs if v]
        if not targets:
            return {"ok": False, "detail": "no notification targets configured"}
        await self._dispatch("Test notification", "Patrearr notifications are working.", "info")
        return {"ok": True, "targets": targets}

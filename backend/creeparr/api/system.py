"""Health, status, logs, tasks and the SSE event stream."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, text
from sse_starlette.sse import EventSourceResponse

from creeparr import __version__
from creeparr.api.deps import get_services
from creeparr.core.errors import NotFound
from creeparr.db.engine import session_scope
from creeparr.db.enums import JobStatus, MediaStatus
from creeparr.db.models import Creator, DownloadJob, MediaItem, Post
from creeparr.downloader.fs import is_writable_dir
from creeparr.logging_setup import ring_buffer
from creeparr.patreon.transport import curl_cffi_available
from creeparr.services import Services

log = logging.getLogger(__name__)
router = APIRouter(tags=["system"])

_writable_cache: dict[str, Any] = {"ts": 0.0, "ok": None}


def _downloads_writable(services: Services) -> bool:
    now = time.monotonic()
    if now - _writable_cache["ts"] > 60 or _writable_cache["ok"] is None:
        _writable_cache["ok"] = all(
            is_writable_dir(r) for r in services.env.download_roots().values()
        )
        _writable_cache["ts"] = now
    return bool(_writable_cache["ok"])


def health_payload(services: Services) -> tuple[dict[str, Any], int]:
    db_ok = True
    try:
        with session_scope(services.session_factory) as s:
            s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    writable = _downloads_writable(services)
    ffmpeg = services.env.resolve_ffmpeg()
    payload = {
        "status": "ok" if db_ok and writable else "degraded",
        "db": db_ok,
        "downloads_writable": writable,
        "ffmpeg": ffmpeg,
        "version": __version__,
    }
    return payload, (200 if db_ok else 503)


@router.get("/system/health")
def system_health(request: Request, services: Services = Depends(get_services)):
    from fastapi.responses import JSONResponse

    payload, code = health_payload(services)
    return JSONResponse(payload, status_code=code)


@router.get("/system/status")
def system_status(services: Services = Depends(get_services)) -> dict[str, Any]:
    import yt_dlp.version

    with session_scope(services.session_factory) as s:
        creators = s.execute(select(func.count()).select_from(Creator)).scalar_one()
        posts = s.execute(select(func.count()).select_from(Post)).scalar_one()
        media_done = s.execute(
            select(func.count())
            .select_from(MediaItem)
            .where(MediaItem.status == MediaStatus.COMPLETED)
        ).scalar_one()
        media_bytes = s.execute(
            select(func.coalesce(func.sum(MediaItem.file_size_bytes), 0))
        ).scalar_one()
        provider_bytes = dict(
            s.execute(
                select(Creator.provider, func.coalesce(func.sum(MediaItem.file_size_bytes), 0))
                .join(MediaItem, MediaItem.creator_id == Creator.id)
                .where(MediaItem.status == MediaStatus.COMPLETED)
                .group_by(Creator.provider)
            ).all()
        )
        queued = s.execute(
            select(func.count())
            .select_from(DownloadJob)
            .where(DownloadJob.status == JobStatus.QUEUED)
        ).scalar_one()
        running = s.execute(
            select(func.count())
            .select_from(DownloadJob)
            .where(DownloadJob.status == JobStatus.RUNNING)
        ).scalar_one()
        failed = s.execute(
            select(func.count())
            .select_from(MediaItem)
            .where(MediaItem.status.in_([MediaStatus.FAILED, MediaStatus.FAILED_PERMANENT]))
        ).scalar_one()
    try:
        usage = shutil.disk_usage(services.env.download_dir)
        disk = {"free_bytes": usage.free, "total_bytes": usage.total, "used_bytes": usage.used}
    except OSError:
        disk = {"free_bytes": None, "total_bytes": None, "used_bytes": None}
    ffmpeg = services.env.resolve_ffmpeg()
    next_scan = services.scheduler.next_scan_at()
    settings = services.settings.get()
    return {
        "version": __version__,
        "started_at": services.started_at.isoformat(),
        "uptime_seconds": int((datetime.now(UTC) - services.started_at).total_seconds()),
        "auth": services.providers.get("patreon").get_auth_status(),
        "providers": services.providers.describe_all(),
        "scan": services.scan_manager.status(),
        "next_scan_at": next_scan.isoformat() if next_scan else None,
        "downloads": services.downloads.status(),
        "counts": {
            "creators": creators,
            "posts": posts,
            "media_completed": media_done,
            "media_bytes": media_bytes,
            "provider_bytes": provider_bytes,
            "queued": queued,
            "running": running,
            "failed": failed,
        },
        "disk": disk,
        "paths": services.env.describe_paths(),
        "ffmpeg": ffmpeg,
        "ytdlp_version": yt_dlp.version.__version__,
        "http_backend": settings.patreon.http_backend,
        "curl_cffi_available": curl_cffi_available(),
    }


@router.get("/system/logs")
def system_logs(
    lines: int = Query(default=500, ge=1, le=5000),
    level: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    return {"lines": ring_buffer.tail(lines, level, search)}


@router.get("/system/tasks")
def system_tasks(services: Services = Depends(get_services)) -> list[dict[str, Any]]:
    return services.scheduler.list_tasks()


@router.post("/system/tasks/{name}/run", status_code=202)
async def run_task(name: str, services: Services = Depends(get_services)):
    if name not in services.scheduler.tasks:
        raise NotFound(f"task {name} not found")
    asyncio.create_task(services.scheduler.run_task(name))
    return {"started": True}


@router.get("/events")
async def events(request: Request, services: Services = Depends(get_services)):
    queue = services.bus.subscribe()

    async def gen() -> AsyncIterator[dict[str, Any]]:
        try:
            yield {
                "event": "hello",
                "data": json.dumps(
                    {"providers": {p.name: p.get_auth_status() for p in services.providers}}
                ),
            }
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    continue
                yield {"event": msg["event"], "data": json.dumps(msg["data"], default=str)}
        finally:
            services.bus.unsubscribe(queue)

    return EventSourceResponse(gen(), ping=20)

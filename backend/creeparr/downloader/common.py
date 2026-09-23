"""Types and constants shared by the download manager's parts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from creeparr.db.enums import MediaSource
from creeparr.downloader.handlers.base import ProgressReporter

PAUSED_KEY = "downloads_paused"
TMP_PREFIX = ".creeparr-tmp-"
EMBED_SOURCES = {MediaSource.EMBED_YOUTUBE, MediaSource.EMBED_VIMEO, MediaSource.EMBED_OTHER}


def _as_utc(value: datetime | None) -> datetime | None:
    """Aggregates like func.min may return a naive datetime; treat it as UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass
class JobContext:
    job_id: int
    attempt: int
    provider: str
    media_id: int
    post_id: int
    creator_id: int
    media_key: str
    kind: str
    source: str
    url: str | None
    remote_file_name: str | None
    mimetype: str | None
    order_index: int
    remote_metadata: dict[str, Any]
    post_ext_id: str
    post_title: str
    post_published_at: datetime | None
    post_first_seen: datetime | None
    post_type: str | None
    post_thumbnail_url: str | None
    post_sidecars_written: bool
    post_urls_fetched_at: datetime | None
    post_can_view: bool
    creator_name: str
    creator_folder: str | None
    creator_vanity: str | None
    campaign_id: str
    embed_provider: str | None


@dataclass
class RunningJob:
    ctx: JobContext
    reporter: ProgressReporter
    task: asyncio.Task[None] | None = None

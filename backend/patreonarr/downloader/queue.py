"""Queue and status bookkeeping shared by the scanner, downloader and API."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from patreonarr.core.events import EventBus
from patreonarr.db.enums import (
    ACTIVE_MEDIA_STATUSES,
    JobStatus,
    MediaKind,
    MediaStatus,
    PostStatus,
)
from patreonarr.db.models import Creator, DownloadJob, MediaItem, Post

KIND_DISABLED_REASON = "kind disabled for creator"


def kind_wanted(creator: Creator, kind: str) -> bool:
    if kind == MediaKind.VIDEO:
        return True
    if kind == MediaKind.IMAGE:
        return creator.include_images
    if kind == MediaKind.AUDIO:
        return creator.include_audio
    return creator.include_attachments


def apply_creator_prefs(creator: Creator, item: MediaItem) -> None:
    """Set `wanted` from creator preferences unless the user skipped it manually."""
    if item.status == MediaStatus.SKIPPED and item.status_reason != KIND_DISABLED_REASON:
        return  # user skip wins
    wanted = kind_wanted(creator, item.kind)
    item.wanted = wanted
    if not wanted and item.status in (
        MediaStatus.DISCOVERED,
        MediaStatus.QUEUED,
        MediaStatus.FAILED,
    ):
        item.status = MediaStatus.SKIPPED
        item.status_reason = KIND_DISABLED_REASON
    elif (
        wanted and item.status == MediaStatus.SKIPPED and item.status_reason == KIND_DISABLED_REASON
    ):
        item.status = MediaStatus.DISCOVERED
        item.status_reason = None


def active_job_for(session: Session, media_item_id: int) -> DownloadJob | None:
    return session.execute(
        select(DownloadJob).where(
            DownloadJob.media_item_id == media_item_id,
            DownloadJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
    ).scalar_one_or_none()


def enqueue_media(
    session: Session, item: MediaItem, *, priority: int = 0, force: bool = False
) -> DownloadJob | None:
    """Move a media item to `queued` and create a job. Returns the job or None if not queued."""
    if not item.wanted and not force:
        return None
    if item.status in (MediaStatus.UNSUPPORTED_DRM, MediaStatus.UNSUPPORTED) and not force:
        return None
    if item.status == MediaStatus.NO_ACCESS:
        return None
    if item.status == MediaStatus.COMPLETED and not force:
        return None
    if item.status == MediaStatus.DOWNLOADING:
        return None
    existing = active_job_for(session, item.id)
    if existing is not None:
        if existing.priority < priority:
            existing.priority = priority
        return existing
    if force:
        item.wanted = True
    item.status = MediaStatus.QUEUED
    item.status_reason = None
    item.next_retry_at = None
    job = DownloadJob(
        media_item_id=item.id,
        post_id=item.post_id,
        creator_id=item.creator_id,
        status=JobStatus.QUEUED,
        priority=priority,
        attempt=item.attempts + 1,
    )
    session.add(job)
    session.flush()
    return job


def recompute_post_status(post: Post) -> None:
    if not post.current_user_can_view:
        post.status = PostStatus.NO_ACCESS
        return
    items = list(post.media_items)
    if post.status == PostStatus.SKIPPED and all(
        (not i.wanted) or i.status == MediaStatus.SKIPPED for i in items
    ):
        return
    wanted = [i for i in items if i.wanted and i.status != MediaStatus.SKIPPED]
    if not wanted:
        post.status = PostStatus.NO_MEDIA
        return
    statuses = {i.status for i in wanted}
    if statuses & ACTIVE_MEDIA_STATUSES:
        post.status = PostStatus.PENDING
    elif statuses == {MediaStatus.COMPLETED}:
        post.status = PostStatus.COMPLETED
    elif MediaStatus.COMPLETED in statuses:
        post.status = PostStatus.PARTIAL
    elif statuses <= {MediaStatus.UNSUPPORTED, MediaStatus.UNSUPPORTED_DRM}:
        post.status = PostStatus.UNSUPPORTED
    elif statuses <= {MediaStatus.CANCELLED}:
        post.status = PostStatus.PENDING
    else:
        post.status = PostStatus.PARTIAL


def publish_post_changed(bus: EventBus | None, post: Post) -> None:
    if bus is not None:
        bus.publish(
            "post.changed", {"id": post.id, "creator_id": post.creator_id, "status": post.status}
        )


def utcnow() -> datetime:
    return datetime.now(UTC)

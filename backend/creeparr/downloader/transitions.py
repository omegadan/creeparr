"""Recording job outcomes in the database: progress, completion, failure, cancel."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from creeparr.core.history import record_event
from creeparr.db.engine import session_scope
from creeparr.db.enums import (
    EventType,
    JobStatus,
    MediaKind,
    MediaStatus,
)
from creeparr.db.models import Creator, DownloadJob, MediaItem, Post
from creeparr.downloader.common import JobContext
from creeparr.downloader.fs import (
    set_times,
    try_hardlink,
)
from creeparr.downloader.handlers.base import DownloadResult
from creeparr.downloader.queue import publish_post_changed, recompute_post_status
from creeparr.downloader.sidecars import write_nfo, write_text_sidecars

log = logging.getLogger(__name__)


class TransitionsMixin:
    """DownloadManager's job state transitions."""

    def _write_sidecars(self, ctx: JobContext, post_dir: Path) -> None:
        post_dir.mkdir(parents=True, exist_ok=True)
        if not self.settings.get().naming.write_sidecars:
            return
        with session_scope(self._factory) as s:
            post = s.get(Post, ctx.post_id)
            creator = s.get(Creator, ctx.creator_id)
            if post is None or creator is None:
                return
            rel = str(post_dir.relative_to(self.env.download_root(ctx.provider)))
            if post.folder_path != rel:
                post.folder_path = rel
            if post.sidecars_written:
                return
            try:
                write_text_sidecars(post_dir, post, creator)
                post.sidecars_written = True
            except OSError as exc:
                log.warning("could not write sidecars for post %s: %s", post.post_id, exc)

    def _on_progress(self, job_id: int, data: dict[str, Any]) -> None:
        self.bus.publish("job.progress", {"id": job_id, **data})
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Direct downloads report from the event loop; a blocking SQLite write here
            # would stall the whole server while the scanner holds the write lock.
            loop.run_in_executor(None, self._write_progress, job_id, data)
        else:
            self._write_progress(job_id, data)  # yt-dlp's hooks run in its own thread

    def _write_progress(self, job_id: int, data: dict[str, Any]) -> None:
        try:
            with session_scope(self._factory) as s:
                job = s.get(DownloadJob, job_id)
                if job is None or job.status != JobStatus.RUNNING:
                    return  # finished meanwhile; don't overwrite its final state
                for key in (
                    "progress_percent",
                    "bytes_downloaded",
                    "total_bytes",
                    "speed_bps",
                    "eta_seconds",
                    "stage",
                ):
                    if key in data:
                        setattr(job, key, data[key])
        except Exception:  # noqa: BLE001
            log.debug("progress write failed", exc_info=True)

    def _dedupe(self, ctx: JobContext, result: DownloadResult) -> bool:
        """If another completed file has the same sha256, hardlink to it to save disk."""
        if not (self.settings.get().downloads.deduplicate and result.sha256):
            return False
        with session_scope(self._factory) as s:
            twin = s.execute(
                select(MediaItem.file_path, Creator.provider)
                .join(Creator, Creator.id == MediaItem.creator_id)
                .where(
                    MediaItem.sha256 == result.sha256,
                    MediaItem.id != ctx.media_id,
                    MediaItem.status == MediaStatus.COMPLETED,
                    MediaItem.file_path.is_not(None),
                )
                .limit(1)
            ).first()
        if twin is None:
            return False
        # The twin's path is relative to its own provider's root, not any root.
        source = self.env.download_root(twin.provider) / twin.file_path
        if source.is_file() and try_hardlink(result.path, source):
            log.info("[job %s] deduplicated (sha %s)", ctx.job_id, result.sha256[:12])
            return True
        return False

    def _maybe_write_nfo(self, ctx: JobContext, result: DownloadResult) -> None:
        if ctx.kind != MediaKind.VIDEO or not self.settings.get().naming.write_nfo:
            return
        with session_scope(self._factory) as s:
            post = s.get(Post, ctx.post_id)
            creator = s.get(Creator, ctx.creator_id)
            if post and creator:
                try:
                    write_nfo(result.path.parent, post, creator, result.path.name)
                except OSError as exc:
                    log.debug("nfo write failed: %s", exc)

    def _load_job_media(self, s: Session, ctx: JobContext) -> MediaItem | None:
        return s.execute(
            select(MediaItem)
            .options(selectinload(MediaItem.post).selectinload(Post.media_items))
            .where(MediaItem.id == ctx.media_id)
        ).scalar_one_or_none()

    def _finish_orphaned_job(self, s: Session, ctx: JobContext, job: DownloadJob | None) -> None:
        """The media row vanished mid-job (creator deleted, or the post edited it away)."""
        log.info("[job %s] media item %s no longer exists; dropping job", ctx.job_id, ctx.media_id)
        if job is not None:
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(UTC)
            job.stage = None
        self.bus.publish(
            "job.finished",
            {"id": ctx.job_id, "status": JobStatus.CANCELLED, "media_item_id": ctx.media_id},
        )

    def _complete_job(self, ctx: JobContext, result: DownloadResult) -> None:
        self._dedupe(ctx, result)
        self._maybe_write_nfo(ctx, result)
        # Stamp the file and its post folder with the post's publish date.
        if ctx.post_published_at:
            set_times(result.path, ctx.post_published_at)
            set_times(result.path.parent, ctx.post_published_at)
        rel = str(result.path.relative_to(self.env.download_root(ctx.provider)))
        with session_scope(self._factory) as s:
            job = s.get(DownloadJob, ctx.job_id)
            media = self._load_job_media(s, ctx)
            if media is None:
                self._finish_orphaned_job(s, ctx, job)
                return
            now = datetime.now(UTC)
            media.status = MediaStatus.COMPLETED
            media.status_reason = None
            media.file_path = rel
            media.file_size_bytes = result.size
            media.sha256 = result.sha256
            media.metadata_embedded = result.metadata_embedded
            media.completed_at = now
            media.last_error = None
            media.next_retry_at = None
            if job is not None:
                job.status = JobStatus.COMPLETED
                job.finished_at = now
                job.stage = None
                job.progress_percent = 100.0
                job.bytes_downloaded = result.size
                job.total_bytes = result.size
            recompute_post_status(media.post)
            record_event(
                s,
                self.bus,
                EventType.DOWNLOAD_COMPLETED,
                f"Downloaded {result.path.name} ({ctx.creator_name} / {ctx.post_title})",
                creator_id=ctx.creator_id,
                post_id=ctx.post_id,
                media_item_id=ctx.media_id,
                data={"file_path": rel, "size": result.size, "source": ctx.source},
            )
            publish_post_changed(self.bus, media.post)
        self.bus.publish(
            "job.finished",
            {"id": ctx.job_id, "status": JobStatus.COMPLETED, "media_item_id": ctx.media_id},
        )
        log.info("[job %s] completed %s (%d bytes)", ctx.job_id, rel, result.size)

    def _fail_job(
        self,
        ctx: JobContext,
        error: str,
        error_class: str,
        *,
        retryable: bool,
        count: bool = True,
    ) -> None:
        d = self.settings.get().downloads
        with session_scope(self._factory) as s:
            job = s.get(DownloadJob, ctx.job_id)
            media = self._load_job_media(s, ctx)
            if media is None:
                self._finish_orphaned_job(s, ctx, job)
                return
            now = datetime.now(UTC)
            media.last_error = error
            if count:
                media.attempts += 1
            if job is not None:
                job.status = JobStatus.FAILED
                job.finished_at = now
                job.error = error
                job.error_class = error_class
                job.stage = None
            level = "warning"
            if not retryable:
                if error_class == "drm":
                    media.status = MediaStatus.UNSUPPORTED_DRM
                    media.status_reason = "DRM-protected; cannot be archived"
                    event = EventType.MEDIA_UNSUPPORTED
                elif error_class == "unsupported":
                    media.status = MediaStatus.UNSUPPORTED
                    media.status_reason = error
                    event = EventType.MEDIA_UNSUPPORTED
                elif error_class == "no_access":
                    media.status = MediaStatus.NO_ACCESS
                    media.status_reason = error
                    event = EventType.DOWNLOAD_FAILED
                else:
                    media.status = MediaStatus.FAILED_PERMANENT
                    media.status_reason = error
                    event = EventType.DOWNLOAD_FAILED
                    level = "error"
                media.next_retry_at = None
            elif media.attempts >= d.max_attempts:
                media.status = MediaStatus.FAILED_PERMANENT
                media.status_reason = f"gave up after {media.attempts} attempts: {error}"
                media.next_retry_at = None
                event = EventType.DOWNLOAD_FAILED
                level = "error"
            else:
                media.status = MediaStatus.FAILED
                media.status_reason = error
                backoff = min(
                    d.retry_base_seconds * (2 ** max(media.attempts - 1, 0)), d.retry_cap_seconds
                )
                backoff *= random.uniform(0.8, 1.2)
                if error_class == "auth":
                    backoff = min(backoff, 600)
                media.next_retry_at = now + timedelta(seconds=backoff)
                event = EventType.DOWNLOAD_FAILED
            recompute_post_status(media.post)
            record_event(
                s,
                self.bus,
                event,
                f"{ctx.creator_name} / {ctx.post_title}: {error}",
                level=level,
                creator_id=ctx.creator_id,
                post_id=ctx.post_id,
                media_item_id=ctx.media_id,
                data={
                    "error_class": error_class,
                    "attempt": ctx.attempt,
                    "retry_at": media.next_retry_at.isoformat() if media.next_retry_at else None,
                },
            )
            publish_post_changed(self.bus, media.post)
        self.bus.publish(
            "job.finished",
            {
                "id": ctx.job_id,
                "status": JobStatus.FAILED,
                "media_item_id": ctx.media_id,
                "error": error,
            },
        )
        log.warning("[job %s] failed (%s): %s", ctx.job_id, error_class, error)

    def _cancel_job_db(self, ctx: JobContext) -> None:
        with session_scope(self._factory) as s:
            job = s.get(DownloadJob, ctx.job_id)
            media = self._load_job_media(s, ctx)
            if media is None:
                self._finish_orphaned_job(s, ctx, job)
                return
            if job is not None:
                job.status = JobStatus.CANCELLED
                job.finished_at = datetime.now(UTC)
                job.stage = None
            if media.status == MediaStatus.DOWNLOADING:
                media.status = MediaStatus.CANCELLED
                media.status_reason = "cancelled by user"
            recompute_post_status(media.post)
            record_event(
                s,
                self.bus,
                EventType.DOWNLOAD_CANCELLED,
                f"Cancelled {ctx.creator_name} / {ctx.post_title}",
                creator_id=ctx.creator_id,
                post_id=ctx.post_id,
                media_item_id=ctx.media_id,
            )
            publish_post_changed(self.bus, media.post)
        self.bus.publish(
            "job.finished",
            {"id": ctx.job_id, "status": JobStatus.CANCELLED, "media_item_id": ctx.media_id},
        )

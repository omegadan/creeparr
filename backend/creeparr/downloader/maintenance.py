"""Scheduled upkeep: embed backlog, restamping, verifying files, recovery, pruning."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from creeparr.core.history import record_event
from creeparr.db.engine import session_scope
from creeparr.db.enums import (
    EventType,
    JobStatus,
    MediaKind,
    MediaStatus,
)
from creeparr.db.models import Creator, DownloadJob, MediaItem, Post
from creeparr.downloader.common import TMP_PREFIX
from creeparr.downloader.fs import (
    remove_tree,
    set_times,
    sha256_file,
)
from creeparr.downloader.metadata import embed_metadata, html_to_text
from creeparr.downloader.queue import enqueue_media, publish_post_changed, recompute_post_status

log = logging.getLogger(__name__)


# A root with at least this many archived files, none of them present, is taken to be
# an unmounted share rather than a deliberately emptied archive.
UNMOUNTED_MIN_FILES = 10


def _root_unavailable(root: Path, total: int, present: int) -> bool:
    """Whether a download root looks unmounted, so its files must not count as missing.

    Otherwise a share that failed to mount would mark the whole archive missing and,
    with requeue_missing on, start re-downloading all of it into the empty mount point.
    """
    try:
        if not root.is_dir() or next(root.iterdir(), None) is None:
            return True
    except OSError:
        return True
    return total >= UNMOUNTED_MIN_FILES and present == 0


class MaintenanceMixin:
    """DownloadManager's maintenance tasks."""

    async def embed_backlog(self) -> dict[str, Any]:
        """Embed metadata + cover into already-downloaded videos that lack it."""
        naming = self.settings.get().naming
        if not naming.embed_metadata:
            return {"skipped": True, "reason": "enable 'Embed metadata' in Settings first"}
        ffmpeg = self.env.resolve_ffmpeg()
        if not ffmpeg:
            return {"skipped": True, "reason": "ffmpeg not found"}
        with session_scope(self._factory) as s:
            ids = (
                s.execute(
                    select(MediaItem.id).where(
                        MediaItem.status == MediaStatus.COMPLETED,
                        MediaItem.kind == MediaKind.VIDEO,
                        MediaItem.file_path.is_not(None),
                        MediaItem.metadata_embedded.is_(False),
                    )
                )
                .scalars()
                .all()
            )
        embedded = 0
        for mid in ids:
            try:
                if await self._embed_existing(mid, ffmpeg):
                    embedded += 1
            except Exception:  # noqa: BLE001
                log.exception("backlog embed failed for media %s", mid)
        if embedded:
            self.bus.publish("queue.changed", {})
        return {"embedded": embedded, "candidates": len(ids)}

    def restamp_files(self) -> dict[str, int]:
        """Set every archived file and post folder to its post's publish date."""
        with session_scope(self._factory) as s:
            rows = s.execute(
                select(Post.folder_path, Post.published_at, Creator.provider)
                .join(Creator, Creator.id == Post.creator_id)
                .where(
                    Post.folder_path.is_not(None),
                    Post.published_at.is_not(None),
                    Post.sidecars_written.is_(True),
                )
            ).all()
        files = 0
        dirs = 0
        for folder_rel, published_at, provider in rows:
            post_dir = self.env.download_root(provider) / folder_rel
            if not post_dir.is_dir():
                continue
            try:
                for child in post_dir.iterdir():
                    if child.is_file():
                        set_times(child, published_at)
                        files += 1
                set_times(post_dir, published_at)
                dirs += 1
            except OSError as exc:
                log.debug("restamp failed for %s: %s", post_dir, exc)
        log.info("restamped %d files across %d post folders", files, dirs)
        return {"files": files, "folders": dirs}

    MISSING_REASON = "file not found on disk"

    def verify_files(self) -> dict[str, int]:
        """Check that every archived file still exists on disk.

        Completed items whose file is gone become ``missing``; missing items whose
        file has come back (a re-mounted disk, a restore from backup) become
        ``completed`` again. With ``downloads.requeue_missing`` on, missing items
        are queued for re-download.
        """
        requeue = self.settings.get().downloads.requeue_missing
        checked = missing = restored = requeued = skipped = 0
        with session_scope(self._factory) as s:
            rows = s.execute(
                select(MediaItem.id, MediaItem.status, MediaItem.file_path, Creator.provider)
                .join(Creator, Creator.id == MediaItem.creator_id)
                .where(
                    MediaItem.status.in_([MediaStatus.COMPLETED, MediaStatus.MISSING]),
                    MediaItem.file_path.is_not(None),
                )
            ).all()
        # stat() outside the session: a large archive on a slow share takes a while.
        seen: list[tuple[int, str, Path, bool]] = []
        per_root: dict[Path, list[int]] = {}  # root -> [files recorded, files present]
        for media_id, status, file_rel, provider in rows:
            root = self.env.download_root(provider)
            present = (root / file_rel).is_file()
            seen.append((media_id, status, root, present))
            counts = per_root.setdefault(root, [0, 0])
            counts[0] += 1
            counts[1] += present
        unavailable = {
            root
            for root, (total, present) in per_root.items()
            if _root_unavailable(root, total, present)
        }
        for root in unavailable:
            log.error(
                "verify_files: %s looks unmounted (empty, or none of its %d archived files "
                "are there); leaving its items alone",
                root,
                per_root[root][0],
            )
        changed: list[tuple[int, bool]] = []
        for media_id, status, root, present in seen:
            if root in unavailable:
                skipped += 1
                continue
            checked += 1
            if status == MediaStatus.COMPLETED and not present:
                changed.append((media_id, False))
            elif status == MediaStatus.MISSING and present:
                changed.append((media_id, True))
        for media_id, present in changed:
            with session_scope(self._factory) as s:
                media = s.execute(
                    select(MediaItem)
                    .options(selectinload(MediaItem.post).selectinload(Post.media_items))
                    .where(MediaItem.id == media_id)
                ).scalar_one_or_none()
                if media is None or media.status not in (
                    MediaStatus.COMPLETED,
                    MediaStatus.MISSING,
                ):
                    continue  # changed under us (re-queued, deleted, ...)
                name = PurePosixPath(media.file_path or "").name
                if present:
                    media.status = MediaStatus.COMPLETED
                    media.status_reason = None
                    restored += 1
                    record_event(
                        s,
                        self.bus,
                        EventType.MEDIA_RESTORED,
                        f"{name} is back on disk",
                        creator_id=media.creator_id,
                        post_id=media.post_id,
                        media_item_id=media.id,
                        data={"file_path": media.file_path},
                    )
                else:
                    media.status = MediaStatus.MISSING
                    media.status_reason = self.MISSING_REASON
                    missing += 1
                    record_event(
                        s,
                        self.bus,
                        EventType.MEDIA_MISSING,
                        f"{name} is missing from disk",
                        level="warning",
                        creator_id=media.creator_id,
                        post_id=media.post_id,
                        media_item_id=media.id,
                        data={"file_path": media.file_path},
                    )
                    if requeue and enqueue_media(s, media) is not None:
                        requeued += 1
                recompute_post_status(media.post)
                publish_post_changed(self.bus, media.post)
        if requeued:
            self.notify()
        if missing or restored:
            self.bus.publish("queue.changed", {})
        log.info(
            "verified %d archived files: %d missing, %d restored, %d re-queued, %d skipped",
            checked,
            missing,
            restored,
            requeued,
            skipped,
        )
        return {
            "checked": checked,
            "missing": missing,
            "restored": restored,
            "requeued": requeued,
            "skipped": skipped,
        }

    async def _embed_existing(self, media_id: int, ffmpeg: str) -> bool:
        with session_scope(self._factory) as s:
            media = s.execute(
                select(MediaItem)
                .options(selectinload(MediaItem.post).selectinload(Post.creator))
                .where(MediaItem.id == media_id)
            ).scalar_one_or_none()
            if media is None or media.post is None or media.file_path is None:
                return False
            post, creator = media.post, media.post.creator
            provider_name = creator.provider if creator else "patreon"
            file_rel = media.file_path
            title = post.title or ""
            creator_name = creator.name if creator else ""
            description = html_to_text(post.content_html or post.teaser_text)
            date = post.published_at.strftime("%Y-%m-%d") if post.published_at else ""
            published_at = post.published_at
            thumb_url = post.thumbnail_url
        abs_path = (self.env.download_root(provider_name) / file_rel).resolve()
        if not abs_path.exists():
            return False
        provider = self.providers.get(provider_name)
        metadata = {
            "title": title,
            "artist": creator_name,
            "album_artist": creator_name,
            "comment": description,
            "description": description,
            "date": date,
        }
        thumb_path: Path | None = None
        if thumb_url:
            thumb_path = await self._fetch_thumbnail(
                provider, thumb_url, abs_path.with_name(f".creeparr-cover-{media_id}.jpg")
            )

        def _do() -> tuple[int, str | None] | None:
            changed = embed_metadata(ffmpeg, abs_path, metadata, thumb_path)
            if thumb_path and thumb_path.exists():
                thumb_path.unlink(missing_ok=True)
            if not changed:
                return None
            size = abs_path.stat().st_size
            sha = sha256_file(abs_path) if self.settings.get().downloads.compute_sha256 else None
            return size, sha

        done = await self._long(_do)
        if done is None:
            log.warning("could not embed metadata into %s; will retry later", abs_path.name)
            return False
        size, sha = done
        if published_at:
            set_times(abs_path, published_at)
            set_times(abs_path.parent, published_at)
        with session_scope(self._factory) as s:
            media = s.get(MediaItem, media_id)
            if media is not None:
                media.file_size_bytes = size
                media.sha256 = sha
                media.metadata_embedded = True
        return True

    # ---- maintenance ---------------------------------------------------------------

    def _recover_stale_jobs(self) -> None:
        with session_scope(self._factory) as s:
            stale = (
                s.execute(select(DownloadJob).where(DownloadJob.status == JobStatus.RUNNING))
                .scalars()
                .all()
            )
            for job in stale:
                job.status = JobStatus.QUEUED
                job.worker_id = None
                # started_at stays: that start counts toward the hourly cap, or every
                # restart would hand out a fresh batch of download slots.
                job.stage = None
                media = s.get(MediaItem, job.media_item_id)
                if media is not None and media.status == MediaStatus.DOWNLOADING:
                    media.status = MediaStatus.QUEUED
            if stale:
                log.info("re-queued %d jobs interrupted by a restart", len(stale))

    def _sweep_temp_dirs(self) -> None:
        for root in self.env.download_roots().values():
            if not root.exists():
                continue
            try:
                for p in root.rglob(f"{TMP_PREFIX}*"):
                    if p.is_dir():
                        remove_tree(p)
            except OSError as exc:
                log.debug("temp sweep failed for %s: %s", root, exc)

    def requeue_due_retries(self) -> int:
        now = datetime.now(UTC)
        n = 0
        with session_scope(self._factory) as s:
            due = (
                s.execute(
                    select(MediaItem).where(
                        MediaItem.status == MediaStatus.FAILED,
                        MediaItem.wanted.is_(True),
                        MediaItem.next_retry_at.is_not(None),
                        MediaItem.next_retry_at <= now,
                    )
                )
                .scalars()
                .all()
            )
            for item in due:
                if enqueue_media(s, item):
                    n += 1
        if n:
            log.info("re-queued %d failed downloads", n)
            self._kick.set()
        return n

    def prune_jobs(self, keep_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=keep_days)
        with session_scope(self._factory) as s:
            old = (
                s.execute(
                    select(DownloadJob).where(
                        DownloadJob.status.in_(
                            [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
                        ),
                        DownloadJob.finished_at < cutoff,
                    )
                )
                .scalars()
                .all()
            )
            for job in old:
                s.delete(job)
            return len(old)

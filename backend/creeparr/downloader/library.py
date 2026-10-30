"""Upkeep of the library: checking archived files are still on disk, restamping their
dates, and embedding metadata into archived videos that lack it."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from creeparr.config import EnvConfig
from creeparr.core.events import EventBus
from creeparr.core.lifecycle import MediaLifecycle
from creeparr.core.settings_service import SettingsService
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import MediaKind, MediaStatus
from creeparr.db.models import Creator, MediaItem, Post
from creeparr.downloader.embed import Embedder
from creeparr.downloader.fs import set_times

log = logging.getLogger(__name__)

# A root with at least this many archived files, none of them present, is taken to be
# an unmounted share rather than a deliberately emptied archive.
UNMOUNTED_MIN_FILES = 10


def root_unavailable(root: Path, total: int, present: int) -> bool:
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


class Library:
    def __init__(
        self,
        env: EnvConfig,
        session_factory: SessionFactory,
        settings: SettingsService,
        bus: EventBus,
        lifecycle: MediaLifecycle,
        embedder: Embedder,
    ) -> None:
        self.env = env
        self._factory = session_factory
        self.settings = settings
        self.bus = bus
        self.lifecycle = lifecycle
        self._embedder = embedder

    def verify_files(self) -> dict[str, int]:
        """Check that every archived file still exists on disk.

        Completed items whose file is gone become missing; missing items whose file has
        come back (a re-mounted disk, a restore from backup) become completed again. With
        `downloads.requeue_missing` on, missing items are queued for re-download.
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
            if root_unavailable(root, total, present)
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
                if media is None:
                    continue
                # Only if nothing changed it meanwhile (re-queued, already flipped, ...).
                if present and media.status == MediaStatus.MISSING:
                    self.lifecycle.file_found(s, media)
                    restored += 1
                elif not present and media.status == MediaStatus.COMPLETED:
                    missing += 1
                    if self.lifecycle.file_missing(s, media, requeue=requeue):
                        requeued += 1
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

    async def embed_backlog(self) -> dict[str, Any]:
        """Embed metadata and a cover into archived videos that lack it."""
        if not self.settings.get().naming.embed_metadata:
            return {"skipped": True, "reason": "enable 'Embed metadata' in Settings first"}
        if not self._embedder.available:
            return {"skipped": True, "reason": "ffmpeg not found"}
        with session_scope(self._factory) as s:
            rows = s.execute(
                select(MediaItem.id, MediaItem.file_path, Creator.provider)
                .join(Creator, Creator.id == MediaItem.creator_id)
                .where(
                    MediaItem.status == MediaStatus.COMPLETED,
                    MediaItem.kind == MediaKind.VIDEO,
                    MediaItem.file_path.is_not(None),
                    MediaItem.metadata_embedded.is_(False),
                )
            ).all()
        embedded = 0
        for media_id, file_rel, provider in rows:
            try:
                if await self._embed_one(media_id, self.env.download_root(provider) / file_rel):
                    embedded += 1
            except Exception:  # noqa: BLE001
                log.exception("backlog embed failed for media %s", media_id)
        if embedded:
            self.bus.publish("queue.changed", {})
        return {"embedded": embedded, "candidates": len(rows)}

    async def _embed_one(self, media_id: int, path: Path) -> bool:
        path = path.resolve()
        if not path.exists():
            return False
        result = await self._embedder.embed(media_id, path)
        if result is None:
            log.warning("could not embed metadata into %s; will retry later", path.name)
            return False
        with session_scope(self._factory) as s:
            media = s.get(MediaItem, media_id)
            if media is None:
                return True
            media.file_size_bytes = result.size
            media.sha256 = result.sha256
            media.metadata_embedded = True
            published_at = media.post.published_at if media.post else None
        if published_at:
            set_times(path, published_at)
            set_times(path.parent, published_at)
        return True

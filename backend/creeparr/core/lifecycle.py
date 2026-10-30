"""The media lifecycle: how media and posts change status in response to actions, scans and
downloads.

User actions, post syncs, downloads and the file checks change a media item's status,
`wanted` flag and attempt count only through `MediaLifecycle`, and a post's status is only
ever derived from its media. The methods work in the caller's session and leave it flushed.
The events and worker wake-ups a change calls for are sent once that session commits, and
dropped if it rolls back.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import event, or_, select, update
from sqlalchemy.orm import Session, selectinload

from creeparr.core.downloads import (
    Claim,
    Completed,
    Failed,
    FailureKind,
    Outcome,
    mentions_drm,
)
from creeparr.core.events import EventBus
from creeparr.core.history import added_payload, record_event
from creeparr.core.settings_service import SettingsService
from creeparr.core.state import get_state, set_state
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import (
    ACTIVE_MEDIA_STATUSES,
    FAILED_MEDIA_STATUSES,
    EventType,
    JobStage,
    JobStatus,
    MediaKind,
    MediaStatus,
    PostStatus,
)
from creeparr.db.models import Creator, DownloadJob, MediaItem, Post
from creeparr.providers.models import MediaSpec, PostResource

if TYPE_CHECKING:
    from creeparr.providers.registry import ProviderRegistry


class PostSource(Protocol):
    """What syncing a post asks its provider (a ProviderService)."""

    def resolve_media(self, post: PostResource) -> list[MediaSpec]: ...

    def post_changed(self, stored_raw: dict[str, Any] | None, post: PostResource) -> bool: ...


log = logging.getLogger(__name__)

# Rows written under older rules are brought up to date once per version (see reconcile).
#   1: post statuses re-derived; queued downloads of unwanted media cancelled
#   2: yt-dlp's DRM failures moved from failed permanently to unsupported (DRM)
#   3: YouTube-channel videos with a cover marked not embedded (their covers never fetched)
RULES_VERSION = 3
RULES_KEY = "media_lifecycle_rules"

KIND_DISABLED_REASON = "kind disabled for creator"
USER_SKIP_REASON = "skipped by user"
REMOVED_REASON = "removed from queue"
CLEARED_REASON = "cleared from failed list"
NO_ACCESS_REASON = "post is not accessible with the current pledge"
CANCELLED_REASON = "cancelled by user"
DRM_REASON = "DRM-protected; cannot be archived"
MISSING_REASON = "file not found on disk"

# Stopped by the user or the creator preferences: never downloaded on their own.
_STOPPED = {MediaStatus.SKIPPED, MediaStatus.CANCELLED}
_UNSUPPORTED = {MediaStatus.UNSUPPORTED, MediaStatus.UNSUPPORTED_DRM}
# Only a forced download queues these again.
_FINISHED = {MediaStatus.COMPLETED, *_UNSUPPORTED}
# Media still to download, or failed: a scan that sees its post again renews its links.
_AWAITING_LINKS = {
    MediaStatus.QUEUED,
    MediaStatus.FAILED,
    MediaStatus.FAILED_PERMANENT,
    MediaStatus.MISSING,
}
# Posts per transaction when reconcile re-derives every post's status.
_RECONCILE_BATCH = 500

# Where a failed download leaves its media. Every kind not listed is retried with back-off
# until the attempt limit, then fails permanently.
_FAILURE_STATUS: dict[FailureKind, MediaStatus] = {
    FailureKind.DRM: MediaStatus.UNSUPPORTED_DRM,
    FailureKind.UNSUPPORTED: MediaStatus.UNSUPPORTED,
    FailureKind.NO_ACCESS: MediaStatus.NO_ACCESS,
    FailureKind.FILE_GONE: MediaStatus.FAILED_PERMANENT,
    FailureKind.POST_GONE: MediaStatus.FAILED_PERMANENT,
    FailureKind.NEEDS_COOKIES: MediaStatus.FAILED_PERMANENT,
    FailureKind.UNAVAILABLE: MediaStatus.FAILED_PERMANENT,
}
# A signed-out provider is usually fixed within minutes, so its retries don't back off longer.
_AUTH_BACKOFF_CAP_SECONDS = 600


class Workers(Protocol):
    """What the lifecycle needs from the download workers."""

    def notify(self) -> None:
        """New jobs are queued."""

    def cancel_job(self, job_id: int) -> bool:
        """Stop a running download. The worker records the outcome."""


@dataclass
class SyncResult:
    post: Post
    is_new: bool
    #: The provider reported an edit, an access change or a new file link.
    changed: bool
    queued: int


@dataclass
class _Effects:
    """What one session's changes need announced once it commits."""

    dirty: set[int] = field(default_factory=set)  # posts whose media changed
    posts: dict[int, dict[str, Any]] = field(default_factory=dict)  # post.changed payloads
    history: list[dict[str, Any]] = field(default_factory=list)  # history.added payloads
    started: list[dict[str, Any]] = field(default_factory=list)  # job.started payloads
    finished: list[dict[str, Any]] = field(default_factory=list)  # job.finished payloads
    queue_changed: bool = False
    wake: bool = False
    cancel: set[int] = field(default_factory=set)  # running jobs to stop


class MediaLifecycle:
    def __init__(
        self,
        session_factory: SessionFactory,
        bus: EventBus,
        settings: SettingsService,
        workers: Workers | None = None,
    ) -> None:
        self._factory = session_factory
        self.bus = bus
        self.settings = settings
        # The download manager. It is attached once both exist, since it uses this too.
        self.workers = workers
        self._key = ("media_lifecycle", id(self))

    # ---- post-level actions (they respect the creator preferences) -----------------

    def download_post(self, session: Session, post: Post, *, force: bool = False) -> None:
        """Queue a post's wanted media. `force` re-downloads archived and unsupported items too.

        Items the user skipped come back unless the creator preferences exclude their kind.
        """
        for item in post.media_items:
            if _user_skipped(item):
                self._set(
                    session, item, status=MediaStatus.DISCOVERED, status_reason=None, wanted=True
                )
                self._apply_prefs(session, post.creator, item)
            if item.wanted:
                self._requeue(session, item, priority=10, force=force)
        self._recompute(session, post)
        session.flush()

    def skip_post(self, session: Session, post: Post) -> None:
        """Skip a post's media that isn't archived yet and stop its downloads."""
        for item in post.media_items:
            self._skip(session, item, USER_SKIP_REASON)
        self._recompute(session, post)
        session.flush()

    def unskip_post(self, session: Session, post: Post) -> None:
        """Bring back a post's skipped and cancelled media, except kinds the creator excludes."""
        for item in post.media_items:
            if item.status in _STOPPED:
                self._set(
                    session, item, status=MediaStatus.DISCOVERED, status_reason=None, wanted=True
                )
                self._apply_prefs(session, post.creator, item)
        self._recompute(session, post)
        session.flush()

    # ---- item-level actions (they override the creator preferences) ----------------

    def retry_media(self, session: Session, item: MediaItem) -> DownloadJob | None:
        """Download one item again now, whatever its status. Returns its job, if queued."""
        self._set(session, item, wanted=True)
        job = self._requeue(session, item, priority=10, force=True)
        self._recompute(session, item.post)
        session.flush()
        return job

    def skip_media(self, session: Session, item: MediaItem) -> None:
        """Skip one item unless it is archived, stopping its download."""
        self._skip(session, item, USER_SKIP_REASON)
        self._recompute(session, item.post)
        session.flush()

    def unskip_media(self, session: Session, item: MediaItem) -> None:
        """Want one item again, even a kind the creator preferences exclude."""
        if item.status in _STOPPED:
            self._set(session, item, status=MediaStatus.DISCOVERED, status_reason=None)
        self._set(session, item, wanted=True)
        self._recompute(session, item.post)
        session.flush()

    def remove_job(self, session: Session, job: DownloadJob) -> None:
        """Take a job off the queue. A queued item becomes cancelled; a running download
        is stopped, and its worker records the cancel."""
        fx = self._effects(session)
        if job.status == JobStatus.RUNNING:
            fx.cancel.add(job.id)
            fx.queue_changed = True
        elif job.status == JobStatus.QUEUED:
            _cancel_queued(job)
            fx.queue_changed = True
            item = job.media_item
            if item is not None:
                self._set(session, item, status=MediaStatus.CANCELLED, status_reason=REMOVED_REASON)
                self._recompute(session, item.post)
        session.flush()

    def retry_failed(self, session: Session) -> int:
        """Queue every failed item again. Returns how many are queued."""
        n = 0
        for item in self._failed_items(session):
            self._set(session, item, wanted=True)
            if self._requeue(session, item, priority=0, force=True) is not None:
                n += 1
            self._recompute(session, item.post)
        session.flush()
        return n

    def clear_failed(self, session: Session) -> int:
        """Skip every failed item, emptying the failed list. Returns how many."""
        items = self._failed_items(session)
        for item in items:
            self._skip(session, item, CLEARED_REASON)
            self._recompute(session, item.post)
        session.flush()
        return len(items)

    # ---- creator preferences -------------------------------------------------------

    def apply_creator_prefs(self, session: Session, creator: Creator) -> None:
        """Re-apply a creator's include switches and auto-download to all its media.

        Newly excluded items are skipped and their queued downloads cancelled (a running
        one finishes). Newly included items are queued when the creator auto-downloads.
        """
        posts = (
            session.execute(
                select(Post)
                .options(selectinload(Post.media_items))
                .where(Post.creator_id == creator.id)
            )
            .scalars()
            .all()
        )
        for post in posts:
            for item in post.media_items:
                self._apply_prefs(session, creator, item)
            self._auto_queue(session, creator, post)
            self._recompute(session, post)
        session.flush()

    # ---- syncing what providers report ---------------------------------------------

    def sync_post(
        self,
        session: Session,
        creator: Creator,
        pr: PostResource,
        source: PostSource,
        *,
        refresh: bool = False,
    ) -> SyncResult:
        """Record what a provider reports about a post and bring its media in line.

        A scan re-syncs the media rows for a new or changed post, and for one with media
        queued or failed, whose links it renews (the post doesn't count as changed);
        `refresh` (the post's Refresh button, or a download renewing its links) always does.
        Links are marked fresh only then. Wanted media is queued when the creator
        auto-downloads.
        """
        post, is_new, changed = _upsert_post(session, creator, pr, source)
        if is_new or changed:
            self._effects(session).dirty.add(post.id)
        awaiting = any(item.status in _AWAITING_LINKS for item in post.media_items)
        if is_new or changed or refresh or awaiting:
            self._sync_media(session, creator, post, source.resolve_media(pr))
            post.urls_fetched_at = datetime.now(UTC)
        queued = self._auto_queue(session, creator, post)
        self._recompute(session, post)
        session.flush()
        return SyncResult(post, is_new, changed, queued)

    def reresolve_all(self, providers: ProviderRegistry) -> dict[str, int]:
        """Re-run each provider's media resolver on stored posts (after a resolver fix).

        The links come from stored data, so they are not marked fresh.
        """
        posts_done = 0
        queued = 0
        with session_scope(self._factory) as s:
            ids = s.execute(select(Post.id)).scalars().all()
        for pid in ids:
            with session_scope(self._factory) as s:
                post = s.execute(
                    select(Post).options(selectinload(Post.media_items)).where(Post.id == pid)
                ).scalar_one_or_none()
                if post is None:
                    continue
                creator = s.get(Creator, post.creator_id)
                if creator is None:
                    continue
                provider = providers.for_creator(creator)
                pr = provider.post_from_raw(post.raw_json or {})
                if pr is not None:
                    self._sync_media(s, creator, post, provider.resolve_media(pr))
                    queued += self._auto_queue(s, creator, post)
                    self._recompute(s, post)
                posts_done += 1
        return {"posts": posts_done, "queued": queued}

    # ---- downloads -----------------------------------------------------------------

    def start_download(self, session: Session, job: DownloadJob, worker_id: str) -> Claim | None:
        """Mark a queued download running and its media downloading.

        Returns None if another worker started it first. `job.started` goes out on commit.
        """
        started = session.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job.id, DownloadJob.status == JobStatus.QUEUED)
            .values(
                status=JobStatus.RUNNING,
                worker_id=worker_id,
                started_at=datetime.now(UTC),
                stage=JobStage.RESOLVING,
                progress_percent=0.0,
            )
        ).rowcount
        if not started:
            return None
        session.refresh(job)
        item = job.media_item
        self._set(session, item, status=MediaStatus.DOWNLOADING)
        self._effects(session).started.append(
            {"id": job.id, "media_item_id": item.id, "post_id": item.post_id}
        )
        session.flush()
        return Claim(job.id, item.id, item.post_id, job.attempt)

    def record_outcome(self, session: Session, claim: Claim, outcome: Outcome) -> None:
        """Record how a download ended: its media's status, attempts and retry time, the job
        and history. `job.finished` and `post.changed` go out on commit.

        If the media item no longer exists (its creator was deleted, or an edit removed it
        from the post), the download is dropped as cancelled, whatever its outcome.
        """
        job = session.get(DownloadJob, claim.job_id)
        item = session.execute(
            select(MediaItem)
            .options(
                selectinload(MediaItem.post).selectinload(Post.media_items),
                selectinload(MediaItem.post).selectinload(Post.creator),
            )
            .where(MediaItem.id == claim.media_id)
        ).scalar_one_or_none()
        finished: dict[str, Any] = {
            "id": claim.job_id,
            "media_item_id": claim.media_id,
            "post_id": claim.post_id,
        }
        if item is None:
            log.info(
                "[job %s] media item %s no longer exists; dropping job",
                claim.job_id,
                claim.media_id,
            )
            if job is not None:
                _end_job(job, JobStatus.CANCELLED)
            finished["status"] = JobStatus.CANCELLED
        elif isinstance(outcome, Completed):
            self._completed(session, claim, item, job, outcome)
            finished["status"] = JobStatus.COMPLETED
        elif isinstance(outcome, Failed):
            self._failed(session, claim, item, job, outcome)
            finished["status"] = JobStatus.FAILED
            finished["error"] = outcome.message
        else:
            self._cancelled(session, item, job)
            finished["status"] = JobStatus.CANCELLED
        if item is not None:
            self._recompute(session, item.post)
        self._effects(session).finished.append(finished)
        session.flush()

    def retry_due(self) -> int:
        """Queue the failed downloads whose retry time has come. Returns how many."""
        n = 0
        with session_scope(self._factory) as s:
            items = (
                s.execute(
                    select(MediaItem)
                    .options(selectinload(MediaItem.post).selectinload(Post.media_items))
                    .where(
                        MediaItem.status == MediaStatus.FAILED,
                        MediaItem.wanted.is_(True),
                        MediaItem.next_retry_at.is_not(None),
                        MediaItem.next_retry_at <= datetime.now(UTC),
                    )
                )
                .scalars()
                .all()
            )
            for item in items:
                if self._queue_again(s, item) is not None:
                    n += 1
                self._recompute(s, item.post)
        if n:
            log.info("re-queued %d failed downloads", n)
        return n

    def recover_interrupted(self) -> int:
        """Put the downloads a restart interrupted back in the queue. Returns how many.

        Their start still counts toward the hourly limit, or every restart would hand out a
        fresh batch of downloads.
        """
        with session_scope(self._factory) as s:
            jobs = (
                s.execute(
                    select(DownloadJob)
                    .options(selectinload(DownloadJob.media_item))
                    .where(DownloadJob.status == JobStatus.RUNNING)
                )
                .scalars()
                .all()
            )
            for job in jobs:
                job.status = JobStatus.QUEUED
                job.worker_id = None
                job.stage = None
                item = job.media_item
                if item is not None and item.status == MediaStatus.DOWNLOADING:
                    self._set(s, item, status=MediaStatus.QUEUED)
        if jobs:
            log.info("re-queued %d jobs interrupted by a restart", len(jobs))
        return len(jobs)

    # ---- the library's file checks -------------------------------------------------

    def file_missing(self, session: Session, item: MediaItem, *, requeue: bool) -> bool:
        """An archived item's file is gone from disk. It becomes missing and, with `requeue`,
        is queued to download again. Returns whether it was queued."""
        self._set(session, item, status=MediaStatus.MISSING, status_reason=MISSING_REASON)
        self._history(
            session,
            item,
            EventType.MEDIA_MISSING,
            f"{PurePosixPath(item.file_path or '').name} is missing from disk",
            level="warning",
            data={"file_path": item.file_path},
        )
        queued = requeue and self._queue_again(session, item) is not None
        self._effects(session).queue_changed = True  # the failed list shows missing media
        self._recompute(session, item.post)
        session.flush()
        return queued

    def file_found(self, session: Session, item: MediaItem) -> None:
        """A missing item's file is back on disk (a re-mounted share, a restored backup)."""
        self._set(session, item, status=MediaStatus.COMPLETED, status_reason=None)
        self._history(
            session,
            item,
            EventType.MEDIA_RESTORED,
            f"{PurePosixPath(item.file_path or '').name} is back on disk",
            data={"file_path": item.file_path},
        )
        self._effects(session).queue_changed = True
        self._recompute(session, item.post)
        session.flush()

    # ---- upgrades ------------------------------------------------------------------

    def reconcile(self) -> dict[str, int] | None:
        """Bring rows written under older rules up to date, once per RULES_VERSION.

        Moves media that yt-dlp failed permanently over DRM to unsupported (DRM), cancels
        queued downloads of media that is no longer wanted and re-derives every post's
        status. Coming from below v3, it marks YouTube-channel videos with a cover not
        embedded, so the embed backlog adds their covers. Returns what changed, or None when
        the rows were already current.
        """
        with session_scope(self._factory) as s:
            applied = get_state(s, RULES_KEY, 0)
            if applied >= RULES_VERSION:
                return None
            covers = 0
            if applied < 3:
                covers = s.execute(
                    update(MediaItem)
                    .where(
                        MediaItem.status == MediaStatus.COMPLETED,
                        MediaItem.kind == MediaKind.VIDEO,
                        MediaItem.metadata_embedded.is_(True),
                        MediaItem.post_id.in_(
                            select(Post.id)
                            .join(Creator, Creator.id == Post.creator_id)
                            .where(Creator.provider == "youtube", Post.thumbnail_url != "")
                        ),
                    )
                    .values(metadata_embedded=False)
                    .execution_options(synchronize_session=False)
                ).rowcount
            drm = [
                item
                for item in s.execute(
                    select(MediaItem).where(
                        MediaItem.status == MediaStatus.FAILED_PERMANENT,
                        MediaItem.last_error.ilike("%drm%"),
                    )
                ).scalars()
                if mentions_drm(item.last_error)
            ]
            for item in drm:
                self._set(s, item, status=MediaStatus.UNSUPPORTED_DRM, status_reason=DRM_REASON)
            jobs = (
                s.execute(
                    select(DownloadJob)
                    .join(MediaItem, MediaItem.id == DownloadJob.media_item_id)
                    .where(
                        DownloadJob.status == JobStatus.QUEUED,
                        or_(MediaItem.wanted.is_(False), MediaItem.status == MediaStatus.SKIPPED),
                    )
                )
                .scalars()
                .all()
            )
            for job in jobs:
                _cancel_queued(job)
            if jobs:
                self._effects(s).queue_changed = True
            ids = s.execute(select(Post.id).order_by(Post.id)).scalars().all()
        posts_changed = 0
        for start in range(0, len(ids), _RECONCILE_BATCH):
            with session_scope(self._factory) as s:
                posts = (
                    s.execute(
                        select(Post)
                        .options(selectinload(Post.media_items))
                        .where(Post.id.in_(ids[start : start + _RECONCILE_BATCH]))
                    )
                    .scalars()
                    .all()
                )
                for post in posts:
                    before = post.status
                    self._recompute(s, post)
                    posts_changed += post.status != before
        with session_scope(self._factory) as s:
            set_state(s, RULES_KEY, RULES_VERSION)
        log.info(
            "media lifecycle rules v%d: %d post statuses updated, %d queued downloads of "
            "unwanted media cancelled, %d DRM failures marked unsupported, %d YouTube videos "
            "marked not embedded",
            RULES_VERSION,
            posts_changed,
            len(jobs),
            len(drm),
            covers,
        )
        return {"posts": posts_changed, "jobs": len(jobs), "drm": len(drm), "covers": covers}

    # ---- rules ---------------------------------------------------------------------

    def _requeue(
        self, session: Session, item: MediaItem, *, priority: int, force: bool
    ) -> DownloadJob | None:
        """Queue an item the user asked for, with a fresh count of attempts. Returns its job,
        or None if it can't be queued."""
        if item.status in (MediaStatus.NO_ACCESS, MediaStatus.DOWNLOADING):
            return None
        if item.status in _FINISHED and not force:
            return None
        job = _active_job(session, item.id)
        if job is not None:
            if job.priority < priority:
                job.priority = priority
                self._effects(session).queue_changed = True
            return job
        if item.status != MediaStatus.DISCOVERED:
            # A retry or re-download: the old link may be what failed, so it is renewed first.
            item.post.urls_fetched_at = None
        self._set(session, item, attempts=0)
        return self._add_job(session, item, priority=priority)

    def _auto_queue(self, session: Session, creator: Creator, post: Post) -> int:
        """Queue a post's newly found wanted media, if the creator auto-downloads it."""
        if not _should_auto_queue(creator, post):
            return 0
        n = 0
        for item in post.media_items:
            if (
                item.wanted
                and item.status == MediaStatus.DISCOVERED
                and _active_job(session, item.id) is None
            ):
                self._add_job(session, item, priority=0)
                n += 1
        return n

    def _queue_again(self, session: Session, item: MediaItem) -> DownloadJob | None:
        """Queue a wanted item again on its own (a retry that's due, a missing file), keeping
        its count of attempts. Returns its job, or None if it can't be queued."""
        if not item.wanted or item.status in (
            MediaStatus.NO_ACCESS,
            MediaStatus.DOWNLOADING,
            *_FINISHED,
        ):
            return None
        return _active_job(session, item.id) or self._add_job(session, item, priority=0)

    def _add_job(self, session: Session, item: MediaItem, *, priority: int) -> DownloadJob:
        self._set(session, item, status=MediaStatus.QUEUED, status_reason=None)
        job = _new_job(session, item, priority)
        fx = self._effects(session)
        fx.queue_changed = True
        fx.wake = True
        return job

    def _completed(
        self,
        session: Session,
        claim: Claim,
        item: MediaItem,
        job: DownloadJob | None,
        done: Completed,
    ) -> None:
        now = datetime.now(UTC)
        self._set(
            session, item, status=MediaStatus.COMPLETED, status_reason=None, next_retry_at=None
        )
        item.file_path = done.file_path
        item.file_size_bytes = done.size
        item.sha256 = done.sha256
        item.metadata_embedded = done.metadata_embedded
        item.completed_at = now
        item.last_error = None
        if job is not None:
            _end_job(job, JobStatus.COMPLETED, now)
            job.progress_percent = 100.0
            job.bytes_downloaded = done.size
            job.total_bytes = done.size
        name = PurePosixPath(done.file_path).name
        self._history(
            session,
            item,
            EventType.DOWNLOAD_COMPLETED,
            f"Downloaded {name} ({_post_label(item.post)})",
            data={"file_path": done.file_path, "size": done.size, "source": item.source},
        )
        log.info("[job %s] completed %s (%d bytes)", claim.job_id, done.file_path, done.size)

    def _failed(
        self,
        session: Session,
        claim: Claim,
        item: MediaItem,
        job: DownloadJob | None,
        failure: Failed,
    ) -> None:
        d = self.settings.get().downloads
        now = datetime.now(UTC)
        attempts = item.attempts + (0 if failure.kind == FailureKind.AUTH else 1)
        status = _FAILURE_STATUS.get(failure.kind)
        retry_at = None
        if status is not None:
            reason = DRM_REASON if status == MediaStatus.UNSUPPORTED_DRM else failure.message
        elif attempts >= d.max_attempts:
            status = MediaStatus.FAILED_PERMANENT
            reason = f"gave up after {attempts} attempts: {failure.message}"
        else:
            status, reason = MediaStatus.FAILED, failure.message
            backoff = min(d.retry_base_seconds * (2 ** max(attempts - 1, 0)), d.retry_cap_seconds)
            backoff *= random.uniform(0.8, 1.2)
            if failure.kind == FailureKind.AUTH:
                backoff = min(backoff, _AUTH_BACKOFF_CAP_SECONDS)
            retry_at = now + timedelta(seconds=backoff)
        self._set(
            session,
            item,
            status=status,
            status_reason=reason,
            attempts=attempts,
            next_retry_at=retry_at,
        )
        item.last_error = failure.message
        if job is not None:
            _end_job(job, JobStatus.FAILED, now)
            job.error = failure.message
            job.error_class = failure.label
        self._history(
            session,
            item,
            EventType.MEDIA_UNSUPPORTED if status in _UNSUPPORTED else EventType.DOWNLOAD_FAILED,
            f"{_post_label(item.post)}: {failure.message}",
            level="error" if status == MediaStatus.FAILED_PERMANENT else "warning",
            data={
                "error_class": failure.label,
                "attempt": claim.attempt,
                "retry_at": retry_at.isoformat() if retry_at else None,
            },
        )
        log.warning("[job %s] failed (%s): %s", claim.job_id, failure.label, failure.message)

    def _cancelled(self, session: Session, item: MediaItem, job: DownloadJob | None) -> None:
        if job is not None:
            _end_job(job, JobStatus.CANCELLED)
        # Skipped or removed while it ran, it keeps that status.
        if item.status == MediaStatus.DOWNLOADING:
            self._set(session, item, status=MediaStatus.CANCELLED, status_reason=CANCELLED_REASON)
        self._history(
            session, item, EventType.DOWNLOAD_CANCELLED, f"Cancelled {_post_label(item.post)}"
        )

    def _history(
        self,
        session: Session,
        item: MediaItem,
        event_type: EventType,
        message: str,
        *,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        row = record_event(
            session,
            None,
            event_type,
            message,
            level=level,
            creator_id=item.creator_id,
            post_id=item.post_id,
            media_item_id=item.id,
            data=data,
        )
        self._effects(session).history.append(added_payload(row))

    def _skip(self, session: Session, item: MediaItem, reason: str) -> None:
        if item.status == MediaStatus.COMPLETED:
            return  # skipping never touches what is already archived
        self._stop_job(session, item, running=True)
        self._set(session, item, status=MediaStatus.SKIPPED, status_reason=reason, wanted=False)

    def _stop_job(self, session: Session, item: MediaItem, *, running: bool) -> None:
        """Cancel the item's queued job; with `running`, also stop a download in progress."""
        job = _active_job(session, item.id)
        if job is None:
            return
        fx = self._effects(session)
        if job.status == JobStatus.QUEUED:
            _cancel_queued(job)
        elif running:
            fx.cancel.add(job.id)
        else:
            return
        fx.queue_changed = True

    def _apply_prefs(self, session: Session, creator: Creator, item: MediaItem) -> None:
        """Set `wanted` from the creator preferences, unless the user skipped the item."""
        if _user_skipped(item):
            return
        wanted = _kind_wanted(creator, item.kind)
        self._set(session, item, wanted=wanted)
        if not wanted and item.status in (
            MediaStatus.DISCOVERED,
            MediaStatus.QUEUED,
            MediaStatus.FAILED,
        ):
            # Its queued download is cancelled; one already running finishes.
            self._stop_job(session, item, running=False)
            self._set(session, item, status=MediaStatus.SKIPPED, status_reason=KIND_DISABLED_REASON)
        elif wanted and item.status == MediaStatus.SKIPPED:
            self._set(session, item, status=MediaStatus.DISCOVERED, status_reason=None)

    def _sync_media(
        self, session: Session, creator: Creator, post: Post, specs: list[MediaSpec]
    ) -> None:
        """Create and update the post's media rows from the resolver's output."""
        existing = {m.media_key: m for m in post.media_items}
        fx = self._effects(session)
        for spec in specs:
            item = existing.get(spec.media_key)
            if item is None:
                item = MediaItem(
                    post_id=post.id,
                    creator_id=creator.id,
                    media_key=spec.media_key,
                    status=MediaStatus.DISCOVERED,
                )
                session.add(item)
                post.media_items.append(item)
                fx.dirty.add(post.id)
            item.kind = spec.kind
            item.source = spec.source
            item.source_url = spec.url
            item.remote_file_name = spec.file_name
            item.mimetype = spec.mimetype
            item.remote_size_bytes = spec.size_bytes
            item.remote_metadata = spec.metadata
            item.order_index = spec.order_index
            if item.status == MediaStatus.NO_ACCESS:
                self._set(session, item, status=MediaStatus.DISCOVERED, status_reason=None)
            self._apply_prefs(session, creator, item)

        # Rows the resolver no longer produces (resolver fix or edited post): drop them unless
        # a file was already archived.
        produced = {spec.media_key for spec in specs}
        for item in list(post.media_items):
            if item.media_key not in produced and item.status != MediaStatus.COMPLETED:
                if item.status in (MediaStatus.QUEUED, MediaStatus.DOWNLOADING):
                    fx.queue_changed = True
                post.media_items.remove(item)
                session.delete(item)
                fx.dirty.add(post.id)

        if not post.current_user_can_view:
            for item in post.media_items:
                if item.status in (MediaStatus.DISCOVERED, MediaStatus.QUEUED, MediaStatus.FAILED):
                    self._set(
                        session, item, status=MediaStatus.NO_ACCESS, status_reason=NO_ACCESS_REASON
                    )
        session.flush()

    def _failed_items(self, session: Session) -> list[MediaItem]:
        return list(
            session.execute(
                select(MediaItem)
                .options(selectinload(MediaItem.post).selectinload(Post.media_items))
                .where(MediaItem.status.in_(FAILED_MEDIA_STATUSES))
            )
            .scalars()
            .all()
        )

    # ---- change tracking -----------------------------------------------------------

    def _set(self, session: Session, item: MediaItem, **values: Any) -> None:
        """Change an item's status fields, noting its post as changed if any differ."""
        changed = False
        for key, value in values.items():
            if getattr(item, key) != value:
                setattr(item, key, value)
                changed = True
        if changed:
            self._effects(session).dirty.add(item.post_id)

    def _recompute(self, session: Session, post: Post) -> None:
        before = post.status
        post.status = derive_post_status(post)
        fx = self._effects(session)
        if post.status != before or post.id in fx.dirty:
            fx.posts[post.id] = {
                "id": post.id,
                "creator_id": post.creator_id,
                "status": post.status,
            }

    def _effects(self, session: Session) -> _Effects:
        fx = session.info.get(self._key)
        if fx is None:
            fx = session.info[self._key] = _Effects()
            if not event.contains(session, "after_commit", self._announce):
                event.listen(session, "after_commit", self._announce)
                event.listen(session, "after_rollback", self._discard)
        return fx

    def _announce(self, session: Session) -> None:
        fx = session.info.pop(self._key, None)
        if fx is None:
            return
        # The change is already committed; a failure here must not look like it wasn't.
        try:
            for payload in fx.history:
                self.bus.publish("history.added", payload)
            for payload in fx.posts.values():
                self.bus.publish("post.changed", payload)
            for payload in fx.started:
                self.bus.publish("job.started", payload)
            for payload in fx.finished:
                self.bus.publish("job.finished", payload)
            if fx.queue_changed:
                self.bus.publish("queue.changed", {})
            if self.workers is not None:
                for job_id in fx.cancel:
                    self.workers.cancel_job(job_id)
                if fx.wake:
                    self.workers.notify()
        except Exception:  # noqa: BLE001
            log.exception("could not announce media lifecycle changes")

    def _discard(self, session: Session) -> None:
        session.info.pop(self._key, None)


# ---- pure rules ------------------------------------------------------------------------


def _kind_wanted(creator: Creator, kind: str) -> bool:
    if kind == MediaKind.VIDEO:
        return True
    if kind == MediaKind.IMAGE:
        return creator.include_images
    if kind == MediaKind.AUDIO:
        return creator.include_audio
    return creator.include_attachments


def _user_skipped(item: MediaItem) -> bool:
    """Skipped by the user rather than by the creator preferences. A user skip wins."""
    return item.status == MediaStatus.SKIPPED and item.status_reason != KIND_DISABLED_REASON


def _should_auto_queue(creator: Creator, post: Post) -> bool:
    if not creator.auto_download:
        return False
    return not (
        creator.download_since and post.published_at and post.published_at < creator.download_since
    )


def _active_job(session: Session, media_item_id: int) -> DownloadJob | None:
    return session.execute(
        select(DownloadJob).where(
            DownloadJob.media_item_id == media_item_id,
            DownloadJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
    ).scalar_one_or_none()


def _new_job(session: Session, item: MediaItem, priority: int) -> DownloadJob:
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


def _cancel_queued(job: DownloadJob) -> None:
    job.status = JobStatus.CANCELLED
    job.finished_at = datetime.now(UTC)


def _end_job(job: DownloadJob, status: JobStatus, now: datetime | None = None) -> None:
    job.status = status
    job.finished_at = now or datetime.now(UTC)
    job.stage = None


def _post_label(post: Post) -> str:
    """The post as history names it: "creator / title"."""
    return f"{post.creator.name if post.creator else ''} / {post.title}"


def _upsert_post(
    session: Session, creator: Creator, pr: PostResource, source: PostSource
) -> tuple[Post, bool, bool]:
    """Insert or update a post row. Returns (post, is_new, changed)."""
    post = session.execute(
        select(Post)
        .options(selectinload(Post.media_items))
        .where(Post.creator_id == creator.id, Post.post_id == pr.id)
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    is_new = post is None
    if post is None:
        post = Post(post_id=pr.id, creator_id=creator.id, first_seen_at=now)
        session.add(post)
        changed = True
    else:
        changed = (
            post.edited_at != pr.edited_at
            or post.current_user_can_view != pr.current_user_can_view
            or post.title != pr.title
            or post.embed_url != pr.embed_url
            or source.post_changed(post.raw_json, pr)
        )
    post.creator_id = creator.id
    post.title = pr.title or ""
    # A listing without a description or date (YouTube's) must not wipe the ones a
    # download backfilled from the video's own metadata.
    if pr.content is not None:
        post.content_html = pr.content
    post.teaser_text = pr.teaser_text
    post.post_type = pr.post_type
    post.url = pr.url
    if pr.published_at is not None:
        post.published_at = pr.published_at
    post.edited_at = pr.edited_at
    post.current_user_can_view = pr.current_user_can_view
    post.thumbnail_url = pr.thumbnail_url
    post.embed_provider = pr.embed_provider
    post.embed_url = pr.embed_url
    post.raw_json = pr.storable_json()
    post.last_seen_at = now
    session.flush()
    return post, is_new, changed


def derive_post_status(post: Post) -> str:
    """A post's status, derived from its media; the lifecycle stores nothing else."""
    if not post.current_user_can_view:
        return PostStatus.NO_ACCESS
    items = list(post.media_items)
    wanted = [i for i in items if i.wanted and i.status not in _STOPPED]
    if not wanted:
        # Nothing left to archive: skipped if the user stopped some of it, otherwise the
        # post never had media the creator preferences include.
        stopped = any(i.status == MediaStatus.CANCELLED or _user_skipped(i) for i in items)
        return PostStatus.SKIPPED if stopped else PostStatus.NO_MEDIA
    statuses = {i.status for i in wanted}
    if statuses & ACTIVE_MEDIA_STATUSES:
        return PostStatus.PENDING
    if statuses == {MediaStatus.COMPLETED}:
        return PostStatus.COMPLETED
    if MediaStatus.COMPLETED in statuses:
        return PostStatus.PARTIAL
    if statuses <= _UNSUPPORTED:
        return PostStatus.UNSUPPORTED
    return PostStatus.PARTIAL

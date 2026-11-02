"""The media lifecycle: how media and posts change status in response to actions and scans.

User actions and post syncs change a media item's status, `wanted` flag and attempt count
only through `MediaLifecycle`, and a post's status is only ever derived from its media. The
methods work in the caller's session and leave it flushed. The events and worker wake-ups
a change calls for are sent once that session commits, and dropped if it rolls back.

The download workers still record job outcomes themselves with the low-level helpers at
the bottom of this module, until that moves here too.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session, selectinload

from creeparr.core.events import EventBus
from creeparr.core.state import get_state, set_state
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import (
    ACTIVE_MEDIA_STATUSES,
    FAILED_MEDIA_STATUSES,
    JobStatus,
    MediaKind,
    MediaStatus,
    PostStatus,
)
from creeparr.db.models import Creator, DownloadJob, MediaItem, Post
from creeparr.providers.models import MediaSpec, PostResource

if TYPE_CHECKING:
    from creeparr.providers.registry import ProviderRegistry

MediaResolver = Callable[[PostResource], list[MediaSpec]]

log = logging.getLogger(__name__)

# Rows written under older rules are brought up to date once per version (see reconcile).
RULES_VERSION = 1
RULES_KEY = "media_lifecycle_rules"

KIND_DISABLED_REASON = "kind disabled for creator"
USER_SKIP_REASON = "skipped by user"
REMOVED_REASON = "removed from queue"
CLEARED_REASON = "cleared from failed list"
NO_ACCESS_REASON = "post is not accessible with the current pledge"

# Stopped by the user or the creator preferences: never downloaded on their own.
_STOPPED = {MediaStatus.SKIPPED, MediaStatus.CANCELLED}
_UNSUPPORTED = {MediaStatus.UNSUPPORTED, MediaStatus.UNSUPPORTED_DRM}
# Only a forced download queues these again.
_FINISHED = {MediaStatus.COMPLETED, *_UNSUPPORTED}
# Posts per transaction when reconcile re-derives every post's status.
_RECONCILE_BATCH = 500


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
    queue_changed: bool = False
    wake: bool = False
    cancel: set[int] = field(default_factory=set)  # running jobs to stop


class MediaLifecycle:
    def __init__(
        self, session_factory: SessionFactory, bus: EventBus, workers: Workers | None = None
    ) -> None:
        self._factory = session_factory
        self.bus = bus
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
        resolver: MediaResolver,
        *,
        refresh: bool = False,
    ) -> SyncResult:
        """Record what a provider reports about a post and bring its media in line.

        A scan re-syncs the media rows only for a new or changed post; `refresh` (the
        post's Refresh button, or a download renewing its links) always does. Links are
        marked fresh only then. Wanted media is queued when the creator auto-downloads.
        """
        post, is_new, changed = _upsert_post(session, creator, pr)
        if is_new or changed:
            self._effects(session).dirty.add(post.id)
        if is_new or changed or refresh:
            self._sync_media(session, creator, post, resolver(pr))
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

    # ---- upgrades ------------------------------------------------------------------

    def reconcile(self) -> dict[str, int] | None:
        """Bring rows written under older rules up to date, once per RULES_VERSION.

        Cancels queued downloads of media that is no longer wanted and re-derives every
        post's status. Returns what changed, or None when the rows were already current.
        """
        with session_scope(self._factory) as s:
            if get_state(s, RULES_KEY, 0) >= RULES_VERSION:
                return None
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
            "unwanted media cancelled",
            RULES_VERSION,
            posts_changed,
            len(jobs),
        )
        return {"posts": posts_changed, "jobs": len(jobs)}

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

    def _add_job(self, session: Session, item: MediaItem, *, priority: int) -> DownloadJob:
        self._set(session, item, status=MediaStatus.QUEUED, status_reason=None)
        job = _new_job(session, item, priority)
        fx = self._effects(session)
        fx.queue_changed = True
        fx.wake = True
        return job

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
        recompute_post_status(post)
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
            for payload in fx.posts.values():
                self.bus.publish("post.changed", payload)
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


def _upsert_post(session: Session, creator: Creator, pr: PostResource) -> tuple[Post, bool, bool]:
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
            or _post_file_url(post) != _post_file_url_from_resource(pr)
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


def _post_file_url(post: Post) -> str | None:
    raw = post.raw_json or {}
    attrs = (raw.get("data") or {}).get("attributes") or {}
    pf = attrs.get("post_file") or {}
    return pf.get("url") if isinstance(pf, dict) else None


def _post_file_url_from_resource(pr: PostResource) -> str | None:
    return (pr.post_file or {}).get("url")


def _derived_status(post: Post) -> str:
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


# ---- low-level helpers the download workers use while they record job outcomes ---------


def enqueue_media(
    session: Session, item: MediaItem, *, priority: int = 0, force: bool = False
) -> DownloadJob | None:
    """Move a media item to `queued` and create a job. Returns the job or None if not queued."""
    if item.status in (MediaStatus.NO_ACCESS, MediaStatus.DOWNLOADING):
        return None
    if not force and (not item.wanted or item.status in _FINISHED):
        return None
    existing = _active_job(session, item.id)
    if existing is not None:
        if existing.priority < priority:
            existing.priority = priority
        return existing
    if force:
        item.wanted = True
    item.status = MediaStatus.QUEUED
    item.status_reason = None
    return _new_job(session, item, priority)


def recompute_post_status(post: Post) -> None:
    """Derive a post's status from its media; nothing sets it any other way."""
    post.status = _derived_status(post)


def publish_post_changed(bus: EventBus | None, post: Post) -> None:
    if bus is not None:
        bus.publish(
            "post.changed", {"id": post.id, "creator_id": post.creator_id, "status": post.status}
        )

"""Walk a creator's posts and sync them into the database."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from patrearr.core.events import EventBus
from patrearr.core.history import record_event
from patrearr.core.settings_service import SettingsService
from patrearr.db.engine import SessionFactory, session_scope
from patrearr.db.enums import EventType, MediaStatus, PostStatus, ScanMode, ScanStatus
from patrearr.db.models import Creator, MediaItem, Post, ScanRun
from patrearr.downloader.queue import (
    apply_creator_prefs,
    enqueue_media,
    publish_post_changed,
    recompute_post_status,
)
from patrearr.providers.errors import (
    AuthError,
    CloudflareChallengeError,
    ForbiddenError,
    ProviderError,
)
from patrearr.providers.models import MediaSpec, PostResource

if TYPE_CHECKING:
    from patrearr.providers.base import ProviderService
    from patrearr.providers.registry import ProviderRegistry

MediaResolver = Callable[[PostResource], list[MediaSpec]]

log = logging.getLogger(__name__)


@dataclass
class PageStats:
    seen: int = 0
    new: int = 0
    updated: int = 0
    queued: int = 0
    consecutive_known: int = 0
    new_post_ids: list[int] = field(default_factory=list)


class ScanCancelled(Exception):
    pass


# ---- sync helpers (run inside a DB session) ------------------------------------------


def upsert_post(session: Session, creator: Creator, pr: PostResource) -> tuple[Post, bool, bool]:
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
    post.content_html = pr.content
    post.teaser_text = pr.teaser_text
    post.post_type = pr.post_type
    post.url = pr.url
    post.published_at = pr.published_at
    post.edited_at = pr.edited_at
    post.current_user_can_view = pr.current_user_can_view
    post.thumbnail_url = pr.thumbnail_url
    post.embed_provider = pr.embed_provider
    post.embed_url = pr.embed_url
    post.raw_json = pr.storable_json()
    post.last_seen_at = now
    post.urls_fetched_at = now
    if is_new:
        post.status = PostStatus.NEW
    session.flush()
    return post, is_new, changed


def _post_file_url(post: Post) -> str | None:
    raw = post.raw_json or {}
    attrs = (raw.get("data") or {}).get("attributes") or {}
    pf = attrs.get("post_file") or {}
    return pf.get("url") if isinstance(pf, dict) else None


def _post_file_url_from_resource(pr: PostResource) -> str | None:
    return (pr.post_file or {}).get("url")


def sync_media_items(
    session: Session, creator: Creator, post: Post, specs: list[MediaSpec]
) -> list[MediaItem]:
    """Create/update media rows for the post from resolver output. Returns new rows."""
    existing = {m.media_key: m for m in post.media_items}
    new_items: list[MediaItem] = []
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
            new_items.append(item)
        item.kind = spec.kind
        item.source = spec.source
        item.source_url = spec.url
        item.remote_file_name = spec.file_name
        item.mimetype = spec.mimetype
        item.remote_size_bytes = spec.size_bytes
        item.remote_metadata = spec.metadata
        item.order_index = spec.order_index
        if item.status == MediaStatus.NO_ACCESS:
            item.status = MediaStatus.DISCOVERED
            item.status_reason = None
        apply_creator_prefs(creator, item)

    # Rows the resolver no longer produces (resolver fix or edited post): drop them unless
    # a file was already archived.
    wanted_keys = {spec.media_key for spec in specs}
    for item in list(post.media_items):
        if item.media_key not in wanted_keys and item.status != MediaStatus.COMPLETED:
            post.media_items.remove(item)
            session.delete(item)

    if not post.current_user_can_view:
        for item in post.media_items:
            if item.status in (MediaStatus.DISCOVERED, MediaStatus.QUEUED, MediaStatus.FAILED):
                item.status = MediaStatus.NO_ACCESS
                item.status_reason = "post is not accessible with the current pledge"
    session.flush()
    return new_items


def should_auto_queue(creator: Creator, post: Post) -> bool:
    if not creator.auto_download:
        return False
    return not (
        creator.download_since and post.published_at and post.published_at < creator.download_since
    )


def auto_queue_post(session: Session, creator: Creator, post: Post) -> int:
    if not should_auto_queue(creator, post):
        return 0
    n = 0
    for item in post.media_items:
        if item.wanted and item.status == MediaStatus.DISCOVERED and enqueue_media(session, item):
            n += 1
    return n


def sync_post(
    session: Session,
    creator: Creator,
    pr: PostResource,
    resolver: MediaResolver,
    *,
    queue: bool = True,
) -> tuple[Post, bool, bool, int]:
    """Upsert a post, its media items and (optionally) queue downloads."""
    post, is_new, changed = upsert_post(session, creator, pr)
    queued = 0
    if is_new or changed:
        sync_media_items(session, creator, post, resolver(pr))
    if queue:
        queued = auto_queue_post(session, creator, post)
    recompute_post_status(post)
    return post, is_new, changed, queued


def reresolve_post(
    session: Session, creator: Creator, post: Post, provider: ProviderService
) -> int:
    """Re-run the media resolver on the stored raw JSON (after a resolver fix)."""
    pr = provider.post_from_raw(post.raw_json or {})
    if pr is None:
        return 0
    sync_media_items(session, creator, post, provider.resolve_media(pr))
    queued = auto_queue_post(session, creator, post)
    recompute_post_status(post)
    return queued


def reresolve_all(
    session_factory: SessionFactory, providers: ProviderRegistry, bus: EventBus | None = None
) -> dict[str, int]:
    posts_done = 0
    queued = 0
    with session_scope(session_factory) as s:
        ids = s.execute(select(Post.id)).scalars().all()
    for pid in ids:
        with session_scope(session_factory) as s:
            post = s.execute(
                select(Post).options(selectinload(Post.media_items)).where(Post.id == pid)
            ).scalar_one_or_none()
            if post is None:
                continue
            creator = s.get(Creator, post.creator_id)
            if creator is None:
                continue
            queued += reresolve_post(s, creator, post, providers.for_creator(creator))
            posts_done += 1
            publish_post_changed(bus, post)
    return {"posts": posts_done, "queued": queued}


# ---- async scanner -------------------------------------------------------------------


class Scanner:
    def __init__(
        self,
        session_factory: SessionFactory,
        settings: SettingsService,
        bus: EventBus,
        providers: ProviderRegistry,
    ) -> None:
        self._factory = session_factory
        self.settings = settings
        self.bus = bus
        self.providers = providers

    def _start_run(self, creator_id: int, mode: ScanMode, trigger: str) -> tuple[int, Creator]:
        with session_scope(self._factory) as s:
            creator = s.get(Creator, creator_id)
            if creator is None:
                raise ValueError(f"creator {creator_id} not found")
            if mode == ScanMode.AUTO:
                mode = ScanMode.FULL if creator.last_full_scan_at is None else ScanMode.INCREMENTAL
            run = ScanRun(creator_id=creator_id, mode=mode, trigger=trigger)
            s.add(run)
            s.flush()
            record_event(
                s,
                self.bus,
                EventType.SCAN_STARTED,
                f"{mode.capitalize()} scan started for {creator.name}",
                creator_id=creator_id,
                data={"scan_run_id": run.id, "mode": mode},
            )
            s.expunge(creator)
            return run.id, creator

    def _process_page(
        self,
        creator_id: int,
        run_id: int,
        posts: list[PostResource],
        overlap: int,
        resolver: MediaResolver,
    ) -> PageStats:
        stats = PageStats()
        with session_scope(self._factory) as s:
            creator = s.get(Creator, creator_id)
            if creator is None:
                raise ScanCancelled("creator deleted during scan")
            run = s.get(ScanRun, run_id)
            for pr in posts:
                post, is_new, changed, queued = sync_post(s, creator, pr, resolver)
                stats.seen += 1
                stats.queued += queued
                if is_new:
                    stats.new += 1
                    stats.consecutive_known = 0
                    stats.new_post_ids.append(post.id)
                elif changed:
                    stats.updated += 1
                    stats.consecutive_known = 0
                else:
                    stats.consecutive_known += 1
                publish_post_changed(self.bus, post)
            if run is not None:
                run.pages_fetched += 1
                run.posts_seen += stats.seen
                run.posts_new += stats.new
                run.posts_updated += stats.updated
                run.media_queued += stats.queued
        return stats

    def _finish_run(
        self,
        run_id: int,
        creator_id: int,
        status: ScanStatus,
        mode: ScanMode,
        error: str | None = None,
    ) -> None:
        with session_scope(self._factory) as s:
            run = s.get(ScanRun, run_id)
            creator = s.get(Creator, creator_id)
            now = datetime.now(UTC)
            if run is not None:
                run.status = status
                run.finished_at = now
                run.error = error
            if creator is not None:
                creator.last_scan_at = now
                creator.last_scan_status = status
                creator.last_scan_error = error
                if status == ScanStatus.OK and mode == ScanMode.FULL:
                    creator.last_full_scan_at = now
                name = creator.name
            else:
                name = f"creator {creator_id}"
            if status == ScanStatus.OK and run is not None:
                record_event(
                    s,
                    self.bus,
                    EventType.SCAN_COMPLETED,
                    f"Scan of {name} finished: {run.posts_new} new, "
                    f"{run.posts_updated} updated, {run.media_queued} queued",
                    creator_id=creator_id,
                    data={
                        "scan_run_id": run_id,
                        "posts_seen": run.posts_seen,
                        "posts_new": run.posts_new,
                        "posts_updated": run.posts_updated,
                        "media_queued": run.media_queued,
                        "mode": mode,
                    },
                )
            elif status == ScanStatus.ERROR:
                record_event(
                    s,
                    self.bus,
                    EventType.SCAN_FAILED,
                    f"Scan of {name} failed: {error}",
                    level="error",
                    creator_id=creator_id,
                    data={"scan_run_id": run_id},
                )
        self.bus.publish(
            "scan.finished",
            {"scan_run_id": run_id, "creator_id": creator_id, "status": status, "error": error},
        )
        self.bus.publish("creator.changed", {"id": creator_id})

    async def scan_creator(
        self,
        creator_id: int,
        mode: ScanMode = ScanMode.AUTO,
        trigger: str = "manual",
        cancel: asyncio.Event | None = None,
    ) -> int:
        """Run a scan. Returns the scan_run id. Raises PatreonError for auth-class failures."""
        run_id, creator = await asyncio.to_thread(self._start_run, creator_id, mode, trigger)
        with session_scope(self._factory) as s:
            effective_mode = ScanMode(s.get(ScanRun, run_id).mode)
        self.bus.publish(
            "scan.started",
            {"scan_run_id": run_id, "creator_id": creator_id, "mode": effective_mode},
        )
        overlap = self.settings.get().scan.overlap_posts
        provider = self.providers.for_creator(creator)
        status = ScanStatus.OK
        error: str | None = None
        try:
            consecutive_known = 0
            async for page in provider.iter_posts(creator.campaign_id):
                if cancel is not None and cancel.is_set():
                    raise ScanCancelled()
                stats = await asyncio.to_thread(
                    self._process_page,
                    creator_id,
                    run_id,
                    page.posts,
                    overlap,
                    provider.resolve_media,
                )
                self.bus.publish(
                    "scan.progress",
                    {
                        "scan_run_id": run_id,
                        "creator_id": creator_id,
                        "seen": stats.seen,
                        "new": stats.new,
                    },
                )
                # Track "known & unchanged" across pages for incremental stop.
                if stats.new == 0 and stats.updated == 0:
                    consecutive_known += stats.seen
                else:
                    consecutive_known = stats.consecutive_known
                if effective_mode == ScanMode.INCREMENTAL and consecutive_known >= overlap:
                    log.info(
                        "incremental scan of %s: %d known posts in a row, stopping",
                        creator.name,
                        consecutive_known,
                    )
                    break
        except ScanCancelled:
            status, error = ScanStatus.CANCELLED, "cancelled"
        except (AuthError, CloudflareChallengeError) as exc:
            status, error = ScanStatus.ERROR, str(exc)
            provider.mark_auth_invalid(exc)
            await asyncio.to_thread(
                self._finish_run, run_id, creator_id, status, effective_mode, error
            )
            raise
        except ForbiddenError as exc:
            status, error = ScanStatus.ERROR, str(exc)
            # Could be an expired session; verify it so the UI can tell the user.
            ok = await provider.check_session()
            await asyncio.to_thread(
                self._finish_run, run_id, creator_id, status, effective_mode, error
            )
            if not ok:
                raise AuthError(str(exc)) from exc
            return run_id
        except ProviderError as exc:
            status, error = ScanStatus.ERROR, str(exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("scan of creator %s crashed", creator_id)
            status, error = ScanStatus.ERROR, f"{exc.__class__.__name__}: {exc}"
        await asyncio.to_thread(self._finish_run, run_id, creator_id, status, effective_mode, error)
        return run_id

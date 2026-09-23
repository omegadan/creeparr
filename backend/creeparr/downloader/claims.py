"""Picking the next job to start: per-provider hourly caps, random pacing, skip rules."""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from creeparr.db.engine import session_scope
from creeparr.db.enums import (
    JobStatus,
    MediaStatus,
)
from creeparr.db.models import Creator, DownloadJob, MediaItem, Post
from creeparr.downloader.common import (
    EMBED_SOURCES,
    JobContext,
    _as_utc,
)

log = logging.getLogger(__name__)


class ClaimsMixin:
    """DownloadManager's claiming and rate-limit logic."""

    def provider_status(self) -> list[dict[str, Any]]:
        """A queue-oriented status for each provider, for the Activity page."""
        disabled = self.providers.disabled_names()
        blocked = self.providers.blocked_names()
        now = datetime.now(UTC)
        with session_scope(self._factory) as s:
            rows = s.execute(
                select(Creator.provider, DownloadJob.status, Creator.enabled, func.count())
                .join(Creator, Creator.id == DownloadJob.creator_id)
                .where(DownloadJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
                .group_by(Creator.provider, DownloadJob.status, Creator.enabled)
            ).all()
            started = self._hourly_starts(s)
        queued: dict[str, int] = {}
        runnable: dict[str, int] = {}  # queued for a creator that is switched on
        running: dict[str, int] = {}
        for provider, status, enabled, count in rows:
            if status == JobStatus.RUNNING:
                running[provider] = running.get(provider, 0) + count
            else:
                queued[provider] = queued.get(provider, 0) + count
                if enabled:
                    runnable[provider] = runnable.get(provider, 0) + count

        out: list[dict[str, Any]] = []
        for provider in self.providers:
            name = provider.name
            q = int(queued.get(name, 0))
            run_q = int(runnable.get(name, 0))
            r = int(running.get(name, 0))
            limit = self._provider_hourly_limit(name)
            recent, oldest, _newest = started.get(name, (0, None, None))
            pacing_until = self._next_allowed_start(name, started)

            next_slot_seconds: int | None = None
            next_slot_at: datetime | None = None
            if name in disabled:
                state = "disabled"
            elif self.paused:
                state = "paused"
            elif r > 0:
                state = "downloading"
            elif run_q == 0:
                # Nothing runnable: either truly empty, or everything queued sits
                # behind creators that are switched off (so it will never start).
                state = "creators_off" if q > 0 else "idle"
            elif limit > 0 and recent >= limit:
                state = "throttled"
                if oldest is not None:
                    next_slot_at = oldest + timedelta(hours=1)
                    next_slot_seconds = max(0, int((next_slot_at - now).total_seconds()))
            elif pacing_until is not None and pacing_until > now:
                # Under the cap, but spreading starts out across the hour.
                state = "pacing"
                next_slot_at = pacing_until
                next_slot_seconds = max(0, int((next_slot_at - now).total_seconds()))
            elif name in blocked:
                state = "blocked"
            else:
                state = "waiting"

            entry: dict[str, Any] = {
                "provider": name,
                "label": provider.label,
                "state": state,
                "queued": q,
                "running": r,
                "hourly_limit": limit,
                "recent_starts": recent,
                "next_slot_seconds": next_slot_seconds,
                "next_slot_at": next_slot_at.isoformat() if next_slot_at is not None else None,
            }
            out.append(entry)
        return out

    # ---- claiming ------------------------------------------------------------------

    def _provider_hourly_limit(self, provider: str) -> int:
        group = getattr(self.settings.get(), provider, None)
        return int(getattr(group, "downloads_per_hour", 0) or 0)

    def _hourly_starts(self, s: Session) -> dict[str, tuple[int, datetime | None, datetime | None]]:
        """Downloads started per provider in the last hour, from persisted job rows.

        Derived from DownloadJob.started_at so the rate-limit window survives a
        restart (an in-memory counter would reset and allow a fresh burst).
        Returns {provider: (count, oldest_start, newest_start)}.
        """
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        rows = s.execute(
            select(
                Creator.provider,
                func.count(),
                func.min(DownloadJob.started_at),
                func.max(DownloadJob.started_at),
            )
            .select_from(DownloadJob)
            .join(Creator, Creator.id == DownloadJob.creator_id)
            .where(DownloadJob.started_at.is_not(None), DownloadJob.started_at >= cutoff)
            .group_by(Creator.provider)
        ).all()
        return {
            provider: (int(count), _as_utc(oldest), _as_utc(newest))
            for provider, count, oldest, newest in rows
        }

    # Random spacing between starts, as a multiple of the even interval (3600 / limit):
    # anywhere from half to one-and-a-half times it, so the pattern is irregular
    # but still averages out to the configured rate.
    PACING_JITTER = (0.5, 1.5)

    @classmethod
    def _pacing_gap(cls, limit: int) -> timedelta:
        return timedelta(seconds=3600 / limit * random.uniform(*cls.PACING_JITTER))

    def _next_allowed_start(
        self, provider: str, started: dict[str, tuple[int, Any, Any]]
    ) -> datetime | None:
        """When spreading is on and a cap is set, the earliest next start for a provider.

        The in-memory value is lost on restart; it is then re-derived from the most
        recent persisted start, so a restart cannot be used to skip the gap.
        """
        limit = self._provider_hourly_limit(provider)
        if limit <= 0 or not self.settings.get().downloads.spread_downloads:
            return None
        if provider not in self._next_start:
            newest = started.get(provider, (0, None, None))[2]
            if newest is None:
                return None
            self._next_start[provider] = newest + self._pacing_gap(limit)
        return self._next_start[provider]

    def _provider_allowed(self, provider: str, started: dict[str, tuple[int, Any, Any]]) -> bool:
        limit = self._provider_hourly_limit(provider)
        if limit <= 0:
            return True
        if started.get(provider, (0, None, None))[0] >= limit:
            return False
        next_start = self._next_allowed_start(provider, started)
        return next_start is None or datetime.now(UTC) >= next_start

    def _schedule_next_start(self, provider: str) -> None:
        """Called right after a start is claimed: pick the random gap to the next one."""
        limit = self._provider_hourly_limit(provider)
        if limit <= 0 or not self.settings.get().downloads.spread_downloads:
            self._next_start.pop(provider, None)
            return
        gap = self._pacing_gap(limit)
        self._next_start[provider] = datetime.now(UTC) + gap
        log.debug("%s: next download no earlier than %.0fs from now", provider, gap.total_seconds())

    def _claim_next(self, worker_id: str) -> JobContext | None:
        with self._claim_lock:
            return self._claim_next_locked(worker_id)

    def _claim_next_locked(self, worker_id: str) -> JobContext | None:
        blocked = self.providers.blocked_names()
        disabled = self.providers.disabled_names()
        cap = self.settings.get().downloads.max_per_creator
        with session_scope(self._factory) as s:
            running_counts = dict(
                s.execute(
                    select(DownloadJob.creator_id, func.count())
                    .where(DownloadJob.status == JobStatus.RUNNING)
                    .group_by(DownloadJob.creator_id)
                ).all()
            )
            started = self._hourly_starts(s)
            throttled = {
                name for name in self.providers.names() if not self._provider_allowed(name, started)
            }
            full_creators = [cid for cid, n in running_counts.items() if n >= cap]
            # Every "can't start yet" rule is part of the query, so however many jobs
            # are queued (a slow archive can have tens of thousands) and however many
            # are waiting on a throttled provider or a busy creator, the first rows
            # returned are ones that can start now; nothing gets starved behind them.
            candidates = (
                s.execute(
                    select(DownloadJob)
                    .join(Creator, Creator.id == DownloadJob.creator_id)
                    .join(MediaItem, MediaItem.id == DownloadJob.media_item_id)
                    .options(
                        selectinload(DownloadJob.media_item)
                        .selectinload(MediaItem.post)
                        .selectinload(Post.creator)
                    )
                    .where(
                        DownloadJob.status == JobStatus.QUEUED,
                        Creator.enabled.is_(True),
                        Creator.provider.not_in(disabled | throttled),
                        DownloadJob.creator_id.not_in(full_creators),
                        or_(
                            Creator.provider.not_in(blocked),
                            MediaItem.source.in_(EMBED_SOURCES),
                        ),
                    )
                    .order_by(DownloadJob.priority.desc(), DownloadJob.created_at)
                    .limit(20)  # a few spare in case one is claimed concurrently
                )
                .scalars()
                .all()
            )
            for job in candidates:
                media = job.media_item
                if media is None or media.post is None:
                    continue
                provider = media.post.creator.provider
                # Conditional update so a job can never be claimed twice.
                claimed = s.execute(
                    update(DownloadJob)
                    .where(DownloadJob.id == job.id, DownloadJob.status == JobStatus.QUEUED)
                    .values(
                        status=JobStatus.RUNNING,
                        worker_id=worker_id,
                        started_at=datetime.now(UTC),
                        stage="resolving",
                        progress_percent=0.0,
                    )
                ).rowcount
                if not claimed:
                    continue
                s.refresh(job)
                media.status = MediaStatus.DOWNLOADING
                s.flush()
                # The claim above set started_at; the persisted row is now the
                # source of truth for the per-hour rate-limit window.
                self._schedule_next_start(provider)
                ctx = self._context(job, media, media.post, media.post.creator)
                self.bus.publish(
                    "job.started",
                    {"id": job.id, "media_item_id": media.id, "post_id": media.post_id},
                )
                return ctx
        return None

    @staticmethod
    def _context(job: DownloadJob, media: MediaItem, post: Post, creator: Creator) -> JobContext:
        return JobContext(
            job_id=job.id,
            attempt=job.attempt,
            provider=creator.provider,
            media_id=media.id,
            post_id=post.id,
            creator_id=creator.id,
            media_key=media.media_key,
            kind=media.kind,
            source=media.source,
            url=media.source_url,
            remote_file_name=media.remote_file_name,
            mimetype=media.mimetype,
            order_index=media.order_index,
            remote_metadata=media.remote_metadata or {},
            post_ext_id=post.post_id,
            post_title=post.title,
            post_published_at=post.published_at,
            post_first_seen=post.first_seen_at,
            post_type=post.post_type,
            post_thumbnail_url=post.thumbnail_url,
            post_sidecars_written=post.sidecars_written,
            post_urls_fetched_at=post.urls_fetched_at,
            post_can_view=post.current_user_can_view,
            creator_name=creator.name,
            creator_folder=creator.folder_name,
            creator_vanity=creator.vanity,
            campaign_id=creator.campaign_id,
            embed_provider=post.embed_provider,
        )

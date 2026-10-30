"""Which queued download starts next: hourly limits, pacing, the per-creator cap, and the
providers and creators that are switched off or signed out."""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from creeparr.core.downloads import Claim
from creeparr.core.lifecycle import MediaLifecycle
from creeparr.core.settings_service import SettingsService
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import EMBED_SOURCES, JobStatus
from creeparr.db.models import Creator, DownloadJob, MediaItem, Post
from creeparr.providers.registry import ProviderRegistry

log = logging.getLogger(__name__)

# Random spacing between starts, as a multiple of the even interval (3600 / limit):
# anywhere from half to one-and-a-half times it, so the pattern is irregular
# but still averages out to the configured rate.
PACING_JITTER = (0.5, 1.5)


class ClaimState(StrEnum):
    """Why a provider's queue is or isn't moving, as the Queue page shows it."""

    DISABLED = "disabled"  # the provider is switched off
    PAUSED = "paused"  # the whole queue is paused
    DOWNLOADING = "downloading"
    IDLE = "idle"  # nothing queued
    CREATORS_OFF = "creators_off"  # everything queued is for creators switched off
    THROTTLED = "throttled"  # the hourly limit is used up
    PACING = "pacing"  # under the limit, spreading starts across the hour
    BLOCKED = "blocked"  # signed out
    WAITING = "waiting"  # free to start next


# Per provider: (starts in the last hour, the oldest of them, the newest of them).
_Starts = dict[str, tuple[int, datetime | None, datetime | None]]


def pacing_gap(limit: int) -> timedelta:
    """A random wait between two starts under an hourly limit of `limit`."""
    return timedelta(seconds=3600 / limit * random.uniform(*PACING_JITTER))


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """Aggregates like func.min may return a naive datetime; treat it as UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Claims:
    def __init__(
        self,
        session_factory: SessionFactory,
        settings: SettingsService,
        providers: ProviderRegistry,
        lifecycle: MediaLifecycle,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._factory = session_factory
        self.settings = settings
        self.providers = providers
        self.lifecycle = lifecycle
        self._clock = clock
        self._lock = threading.Lock()
        # Per provider: earliest time the next download may start while pacing spreads a
        # downloads_per_hour cap out. Lost on restart, then re-derived from the last start.
        self._next_start: dict[str, datetime] = {}

    def next(self, worker_id: str) -> Claim | None:
        """Start the queued download that should go next, if any may start now. Blocking."""
        with self._lock:
            return self._next(worker_id)

    def settings_changed(self) -> None:
        """A changed cap or pacing toggle: re-derive the next allowed start from the last
        persisted start with the new values instead of keeping the old gap."""
        self._next_start.clear()

    def provider_status(self, *, paused: bool) -> list[dict[str, Any]]:
        """A queue-oriented status for each provider, for the Queue page."""
        disabled = self.providers.disabled_names()
        blocked = self.providers.blocked_names()
        now = self._clock()
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
            limit = self._hourly_limit(name)
            recent, oldest, _newest = started.get(name, (0, None, None))
            pacing_until = self._next_allowed_start(name, started)

            next_slot_seconds: int | None = None
            next_slot_at: datetime | None = None
            if name in disabled:
                state = ClaimState.DISABLED
            elif paused:
                state = ClaimState.PAUSED
            elif r > 0:
                state = ClaimState.DOWNLOADING
            elif run_q == 0:
                # Nothing runnable: either truly empty, or everything queued sits
                # behind creators that are switched off (so it will never start).
                state = ClaimState.CREATORS_OFF if q > 0 else ClaimState.IDLE
            elif limit > 0 and recent >= limit:
                state = ClaimState.THROTTLED
                if oldest is not None:
                    next_slot_at = oldest + timedelta(hours=1)
                    next_slot_seconds = max(0, int((next_slot_at - now).total_seconds()))
            elif pacing_until is not None and pacing_until > now:
                # Under the cap, but spreading starts out across the hour.
                state = ClaimState.PACING
                next_slot_at = pacing_until
                next_slot_seconds = max(0, int((next_slot_at - now).total_seconds()))
            elif name in blocked:
                state = ClaimState.BLOCKED
            else:
                state = ClaimState.WAITING

            out.append(
                {
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
            )
        return out

    # ---- rules ---------------------------------------------------------------------

    def _next(self, worker_id: str) -> Claim | None:
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
                name for name in self.providers.names() if not self._allowed(name, started)
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
                claim = self.lifecycle.start_download(s, job, worker_id)
                if claim is None:
                    continue  # another worker got it first
                # Its start is persisted now; that row is what the hourly limit counts.
                self._schedule_next_start(provider)
                return claim
        return None

    def _hourly_limit(self, provider: str) -> int:
        group = getattr(self.settings.get(), provider, None)
        return int(getattr(group, "downloads_per_hour", 0) or 0)

    def _hourly_starts(self, s: Session) -> _Starts:
        """Downloads started per provider in the last hour, from persisted job rows.

        Derived from DownloadJob.started_at so the window survives a restart (an in-memory
        counter would reset and allow a fresh burst).
        """
        cutoff = self._clock() - timedelta(hours=1)
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

    def _next_allowed_start(self, provider: str, started: _Starts) -> datetime | None:
        """When pacing is on and a cap is set, the earliest next start for a provider.

        The in-memory value is lost on restart; it is then re-derived from the most
        recent persisted start, so a restart cannot be used to skip the gap.
        """
        limit = self._hourly_limit(provider)
        if limit <= 0 or not self.settings.get().downloads.spread_downloads:
            return None
        if provider not in self._next_start:
            newest = started.get(provider, (0, None, None))[2]
            if newest is None:
                return None
            self._next_start[provider] = newest + pacing_gap(limit)
        return self._next_start[provider]

    def _allowed(self, provider: str, started: _Starts) -> bool:
        limit = self._hourly_limit(provider)
        if limit <= 0:
            return True
        if started.get(provider, (0, None, None))[0] >= limit:
            return False
        next_start = self._next_allowed_start(provider, started)
        return next_start is None or self._clock() >= next_start

    def _schedule_next_start(self, provider: str) -> None:
        """Right after a start: pick the random gap to the next one."""
        limit = self._hourly_limit(provider)
        if limit <= 0 or not self.settings.get().downloads.spread_downloads:
            self._next_start.pop(provider, None)
            return
        gap = pacing_gap(limit)
        self._next_start[provider] = self._clock() + gap
        log.debug("%s: next download no earlier than %.0fs from now", provider, gap.total_seconds())

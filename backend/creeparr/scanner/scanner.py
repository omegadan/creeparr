"""Walk a creator's posts page by page; the media lifecycle syncs each one into the DB."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from creeparr.core.events import EventBus
from creeparr.core.history import record_event
from creeparr.core.lifecycle import MediaLifecycle, PostSource
from creeparr.core.settings_service import SettingsService
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import EventType, ScanMode, ScanStatus
from creeparr.db.models import Creator, ScanRun
from creeparr.providers.errors import (
    AuthError,
    CloudflareChallengeError,
    ForbiddenError,
    ProviderError,
)
from creeparr.providers.models import PostResource

if TYPE_CHECKING:
    from creeparr.providers.registry import ProviderRegistry

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


# ---- async scanner -------------------------------------------------------------------


class Scanner:
    def __init__(
        self,
        session_factory: SessionFactory,
        settings: SettingsService,
        bus: EventBus,
        providers: ProviderRegistry,
        lifecycle: MediaLifecycle,
    ) -> None:
        self._factory = session_factory
        self.settings = settings
        self.bus = bus
        self.providers = providers
        self.lifecycle = lifecycle

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
        source: PostSource,
    ) -> PageStats:
        stats = PageStats()
        with session_scope(self._factory) as s:
            creator = s.get(Creator, creator_id)
            if creator is None:
                raise ScanCancelled("creator deleted during scan")
            run = s.get(ScanRun, run_id)
            for pr in posts:
                result = self.lifecycle.sync_post(s, creator, pr, source)
                stats.seen += 1
                stats.queued += result.queued
                if result.is_new:
                    stats.new += 1
                    stats.consecutive_known = 0
                    stats.new_post_ids.append(result.post.id)
                elif result.changed:
                    stats.updated += 1
                    stats.consecutive_known = 0
                else:
                    stats.consecutive_known += 1
            if run is not None:
                run.pages_fetched += 1
                run.posts_seen += stats.seen
                run.posts_new += stats.new
                run.posts_updated += stats.updated
                run.media_queued += stats.queued
        return stats

    def close_interrupted_runs(self) -> int:
        """Mark runs left 'running' by a crash or kill as errored. Called on startup."""
        with session_scope(self._factory) as s:
            runs = s.execute(select(ScanRun).where(ScanRun.status == ScanStatus.RUNNING))
            n = 0
            for run in runs.scalars():
                run.status = ScanStatus.ERROR
                run.error = "interrupted (Creeparr stopped during the scan)"
                run.finished_at = run.finished_at or datetime.now(UTC)
                n += 1
        if n:
            log.warning("closed %d scan run(s) interrupted by the last shutdown", n)
        return n

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
            for source_name, page_iter in provider.iter_sources(creator.campaign_id):
                consecutive_known = 0
                async for page in page_iter:
                    if cancel is not None and cancel.is_set():
                        raise ScanCancelled()
                    stats = await asyncio.to_thread(
                        self._process_page,
                        creator_id,
                        run_id,
                        page.posts,
                        overlap,
                        provider,
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
                    if stats.new == 0 and stats.updated == 0:
                        consecutive_known += stats.seen
                    else:
                        consecutive_known = stats.consecutive_known
                    if effective_mode == ScanMode.INCREMENTAL and consecutive_known >= overlap:
                        log.info(
                            "incremental scan of %s [%s]: %d known in a row, next source",
                            creator.name,
                            source_name,
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
        except asyncio.CancelledError:
            # Shutdown mid-scan: close the run now (synchronously; the loop is going
            # away) so it doesn't show as running forever.
            self._finish_run(
                run_id, creator_id, ScanStatus.CANCELLED, effective_mode, "interrupted by shutdown"
            )
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("scan of creator %s crashed", creator_id)
            status, error = ScanStatus.ERROR, f"{exc.__class__.__name__}: {exc}"
        await asyncio.to_thread(self._finish_run, run_id, creator_id, status, effective_mode, error)
        return run_id

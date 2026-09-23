"""Worker pool that turns queued jobs into files on disk.

The pieces live in mixins: claims (what starts next), paths (where files go),
transitions (DB outcomes) and maintenance (scheduled upkeep)."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from creeparr.config import EnvConfig
from creeparr.core.events import EventBus
from creeparr.core.history import record_event
from creeparr.core.settings_service import SettingsService
from creeparr.core.state import get_state, set_state
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import (
    EventType,
    MediaKind,
    MediaSource,
    MediaStatus,
)
from creeparr.db.models import Creator, Post
from creeparr.downloader.claims import ClaimsMixin
from creeparr.downloader.common import (
    EMBED_SOURCES,
    PAUSED_KEY,
    TMP_PREFIX,
    JobContext,
    RunningJob,
)
from creeparr.downloader.fs import (
    free_space_bytes,
    sha256_file,
)
from creeparr.downloader.handlers.base import (
    DownloadCancelled,
    DownloadResult,
    PermanentDownloadError,
    ProgressReporter,
    RetryableDownloadError,
)
from creeparr.downloader.handlers.direct import download_direct
from creeparr.downloader.handlers.ytdlp import YtDlpOptions, normalise_vimeo_url, run_ytdlp
from creeparr.downloader.maintenance import MaintenanceMixin
from creeparr.downloader.metadata import embed_metadata, html_to_text
from creeparr.downloader.paths import PathsMixin, _remove_partials, _remove_tmp_dir
from creeparr.downloader.queue import recompute_post_status
from creeparr.downloader.transitions import TransitionsMixin
from creeparr.patreon.drm import probe_hls_drm
from creeparr.providers.errors import (
    AuthError,
    CloudflareChallengeError,
    ForbiddenError,
    NotFoundError,
    ProviderError,
)
from creeparr.providers.registry import ProviderRegistry
from creeparr.scanner.scanner import sync_media_items, upsert_post

log = logging.getLogger(__name__)


class DownloadManager(ClaimsMixin, PathsMixin, TransitionsMixin, MaintenanceMixin):
    def __init__(
        self,
        env: EnvConfig,
        session_factory: SessionFactory,
        settings: SettingsService,
        bus: EventBus,
        providers: ProviderRegistry,
    ) -> None:
        self.env = env
        self._factory = session_factory
        self.settings = settings
        self.bus = bus
        self.providers = providers
        self._workers: list[asyncio.Task[None]] = []
        self._running: dict[int, RunningJob] = {}
        self._kick = asyncio.Event()
        self._stop = asyncio.Event()
        self.paused_reason: str | None = None
        self._last_disk_check = 0.0
        self._disk_ok = True
        # Per provider: earliest time the next download may start when a
        # downloads_per_hour cap is being spread out (see _pacing_gap).
        self._next_start: dict[str, datetime] = {}
        self._claim_lock = threading.Lock()
        # Destination paths picked by running jobs whose files don't exist yet, so
        # two same-named files of one post downloading at once get distinct names.
        self._reserved_dests: dict[int, set[Path]] = {}
        self._dest_lock = threading.Lock()
        # yt-dlp, ffmpeg and hashing can each hold a thread for minutes to hours. They
        # get their own pool so claims, DB updates and scans (asyncio's default pool,
        # only cpu_count + 4 threads) never queue behind them.
        self._long_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="creeparr-long")

    # ---- lifecycle -----------------------------------------------------------------

    async def start(self) -> None:
        self._stop.clear()
        await asyncio.to_thread(self._recover_stale_jobs)
        await asyncio.to_thread(self._sweep_temp_dirs)
        with session_scope(self._factory) as s:
            self.paused_reason = "user" if get_state(s, PAUSED_KEY, False) else None
        self._resize_workers()

    async def stop(self) -> None:
        self._stop.set()
        for rj in self._running.values():
            rj.reporter.cancel_event.set()
        self._kick.set()
        for t in self._workers:
            t.cancel()
        for t in self._workers:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._workers.clear()

    def _resize_workers(self) -> None:
        want = self.settings.get().downloads.concurrency
        alive = [t for t in self._workers if not t.done()]
        while len(alive) < want:
            idx = len(alive)
            alive.append(asyncio.create_task(self._worker(idx), name=f"download-worker-{idx}"))
        self._workers = alive  # extra workers exit on their own when they see they're surplus

    def apply_settings(self) -> None:
        self._resize_workers()
        # A changed cap or pacing toggle: re-derive the next allowed start from the
        # last persisted start with the new values instead of keeping the old gap.
        self._next_start.clear()
        self._kick.set()

    def notify(self) -> None:
        self._kick.set()

    # ---- pause / resume / cancel ---------------------------------------------------

    @property
    def paused(self) -> bool:
        return self.paused_reason is not None

    def pause(self, reason: str = "user") -> None:
        self.paused_reason = reason
        if reason == "user":
            with session_scope(self._factory) as s:
                set_state(s, PAUSED_KEY, True)
                record_event(s, self.bus, EventType.QUEUE_PAUSED, "Download queue paused")
        self.bus.publish("queue.paused", {"reason": reason})

    def resume(self) -> None:
        self.paused_reason = None
        with session_scope(self._factory) as s:
            set_state(s, PAUSED_KEY, False)
        self.bus.publish("queue.resumed", {})
        self._kick.set()

    def cancel_job(self, job_id: int) -> bool:
        rj = self._running.get(job_id)
        if rj is None:
            return False
        rj.reporter.cancel_event.set()
        return True

    def status(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "paused_reason": self.paused_reason,
            "workers": len([t for t in self._workers if not t.done()]),
            "running_jobs": list(self._running.keys()),
            "free_bytes": min(
                (free_space_bytes(r) for r in self.env.download_roots().values()), default=0
            ),
        }

    # ---- worker loop ---------------------------------------------------------------

    def _check_disk(self) -> bool:
        now = time.monotonic()
        if now - self._last_disk_check < 30:
            return self._disk_ok
        self._last_disk_check = now
        min_free = self.settings.get().downloads.min_free_mb * 1024 * 1024
        roots = self.env.download_roots().values()
        free = min((free_space_bytes(r) for r in roots), default=0)
        ok = free >= min_free
        if not ok and self.paused_reason is None:
            log.error("free space %.1f MB below minimum; pausing downloads", free / 1e6)
            self.pause("disk_full")
        elif ok and self.paused_reason == "disk_full":
            log.info("free space recovered; resuming downloads")
            self.resume()
        self._disk_ok = ok
        return ok

    async def _worker(self, idx: int) -> None:
        worker_id = f"w{idx}"
        while not self._stop.is_set():
            if idx >= self.settings.get().downloads.concurrency:
                return  # surplus after a concurrency decrease
            # Nothing may escape this loop: a dead worker is never restarted, so the pool
            # would silently shrink until downloads stop altogether.
            try:
                await self._worker_step(worker_id)
            except Exception:  # noqa: BLE001
                log.exception("download worker %s hit an unexpected error", worker_id)
                await self._sleep(5)

    async def _worker_step(self, worker_id: str) -> None:
        self._check_disk()
        if self.paused:
            await self._sleep(5)
            return
        ctx = await asyncio.to_thread(self._claim_next, worker_id)
        if ctx is None:
            await self._sleep(5)
            return
        reporter = ProgressReporter(lambda data, jid=ctx.job_id: self._on_progress(jid, data))
        self._running[ctx.job_id] = RunningJob(ctx, reporter)
        try:
            await self._run_job(ctx, reporter)
        except Exception:  # noqa: BLE001
            log.exception("job %s crashed", ctx.job_id)
            try:
                await asyncio.to_thread(
                    self._fail_job, ctx, "internal error", "internal", retryable=True
                )
            except Exception:  # noqa: BLE001
                log.exception("job %s: could not record failure", ctx.job_id)
        finally:
            self._running.pop(ctx.job_id, None)
            with self._dest_lock:
                self._reserved_dests.pop(ctx.job_id, None)
            self._kick.set()

    async def _long(self, fn, *args, **kwargs):  # noqa: ANN001, ANN202
        """Run a long blocking call (download, remux, embed, hash) in the long pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._long_pool, functools.partial(fn, *args, **kwargs))

    async def _sleep(self, seconds: float) -> None:
        self._kick.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._kick.wait(), timeout=seconds)

    # ---- running a job -------------------------------------------------------------

    async def _run_job(self, ctx: JobContext, reporter: ProgressReporter) -> None:
        settings = self.settings.get()
        provider = self.providers.get(ctx.provider)
        label = f"[job {ctx.job_id} media {ctx.media_id}]"
        log.info("%s starting %s %s", label, ctx.source, ctx.post_title)

        # 1. Refresh signed URLs when stale (native media only).
        if ctx.source not in EMBED_SOURCES:
            max_age = timedelta(minutes=settings.downloads.url_max_age_minutes)
            stale = ctx.post_urls_fetched_at is None or (
                datetime.now(UTC) - ctx.post_urls_fetched_at > max_age
            )
            if stale or ctx.attempt > 1 or not ctx.url:
                try:
                    await self._refresh_post(ctx)
                except (AuthError, CloudflareChallengeError) as exc:
                    provider.mark_auth_invalid(exc)
                    await asyncio.to_thread(
                        self._fail_job, ctx, f"auth: {exc}", "auth", retryable=True, count=False
                    )
                    return
                except ForbiddenError as exc:
                    await asyncio.to_thread(
                        self._fail_job, ctx, str(exc), "no_access", retryable=False
                    )
                    return
                except NotFoundError as exc:
                    await asyncio.to_thread(
                        self._fail_job, ctx, f"post gone: {exc}", "not_found", retryable=False
                    )
                    return
                except ProviderError as exc:
                    await asyncio.to_thread(self._fail_job, ctx, str(exc), "api", retryable=True)
                    return
            if not ctx.post_can_view:
                await asyncio.to_thread(
                    self._fail_job, ctx, "post not accessible", "no_access", retryable=False
                )
                return
        if not ctx.url:
            await asyncio.to_thread(
                self._fail_job, ctx, "no download URL", "no_url", retryable=True
            )
            return

        # 2. Post folder and sidecars. A yt-dlp video with no known date (a YouTube
        # back-catalogue entry) learns it from the download's metadata, so its folder
        # name and sidecars wait until then instead of using the first-seen date.
        root = self.env.download_root(ctx.provider)
        post_dir = root / self._post_dir(ctx)
        date_pending = ctx.post_published_at is None and ctx.source not in (
            MediaSource.NATIVE_DIRECT,
            MediaSource.MEDIA_DOWNLOAD,
        )
        if not date_pending:
            await asyncio.to_thread(self._write_sidecars, ctx, post_dir)

        # 3. DRM: flagged by the provider up front, or probed from the HLS playlist.
        if ctx.remote_metadata.get("drm"):
            await asyncio.to_thread(
                self._fail_job, ctx, "DRM-protected media", "drm", retryable=False
            )
            return
        if ctx.source == MediaSource.NATIVE_HLS:
            try:
                drm = await probe_hls_drm(provider.fetch_text, ctx.url)
            except ProviderError as exc:
                await asyncio.to_thread(
                    self._fail_job, ctx, f"HLS probe failed: {exc}", "hls_probe", retryable=True
                )
                return
            if drm:
                await asyncio.to_thread(
                    self._fail_job, ctx, "DRM-protected stream", "drm", retryable=False
                )
                return

        # 4. Download.
        tmp_dir = post_dir / f"{TMP_PREFIX}{ctx.media_id}"
        parts: list[Path] = []  # direct-download .part files, kept only for a retry's resume
        try:
            if ctx.source in (MediaSource.NATIVE_DIRECT, MediaSource.MEDIA_DOWNLOAD):
                native_ext = self._guess_ext(ctx)
                target_ext = self._container_ext(ctx) if ctx.kind == MediaKind.VIDEO else native_ext
                dest = self._reserve_dest(ctx, post_dir / self._file_name(ctx, target_ext))
                parts.append(dest.with_name(dest.name + ".part"))
                reporter.set_stage("downloading")
                if native_ext == target_ext:
                    result = await download_direct(
                        provider,
                        ctx.url,
                        dest,
                        reporter,
                        compute_sha256=settings.downloads.compute_sha256,
                    )
                else:
                    tmp_file = dest.with_name(dest.stem + f".src.{native_ext}")
                    parts.append(tmp_file.with_name(tmp_file.name + ".part"))
                    dl = await download_direct(
                        provider,
                        ctx.url,
                        tmp_file,
                        reporter,
                        compute_sha256=False,
                    )
                    result = await self._long(
                        self._remux_to, ctx, dl.path, dest, settings.downloads.compute_sha256
                    )
            else:
                url = ctx.url
                if ctx.source == MediaSource.EMBED_VIMEO:
                    url = normalise_vimeo_url(url)
                opts = YtDlpOptions(
                    headers=provider.media_headers(),
                    cookiefile=str(provider.cookie_file) if provider.cookie_file.exists() else None,
                    video_format=settings.downloads.video_format,
                    container=self._container_ext(ctx),
                    ffmpeg_location=self.env.resolve_ffmpeg(),
                    fragment_concurrency=settings.downloads.hls_fragment_concurrency,
                    impersonate=provider.ytdlp_impersonate,
                    remote_components=settings.downloads.ytdlp_remote_components,
                )
                reporter.set_stage("downloading")
                produced, yt_meta = await self._long(run_ytdlp, url, tmp_dir, opts, reporter, label)
                await self._backfill_date(ctx, yt_meta)
                if date_pending:
                    post_dir = root / self._post_dir(ctx)
                    await asyncio.to_thread(self._write_sidecars, ctx, post_dir)
                reporter.set_stage("verifying")
                result = await self._long(
                    self._finalise_ytdlp, ctx, post_dir, produced, settings.downloads.compute_sha256
                )
        except DownloadCancelled:
            _remove_partials(parts)
            _remove_tmp_dir(tmp_dir)
            await asyncio.to_thread(self._cancel_job_db, ctx)
            return
        except PermanentDownloadError as exc:
            _remove_partials(parts)
            _remove_tmp_dir(tmp_dir)
            await asyncio.to_thread(self._fail_job, ctx, str(exc), exc.error_class, retryable=False)
            return
        except RetryableDownloadError as exc:
            await asyncio.to_thread(self._fail_job, ctx, str(exc), exc.error_class, retryable=True)
            return
        if ctx.kind == MediaKind.VIDEO and settings.naming.embed_metadata:
            result = await self._embed_metadata(ctx, provider, result, reporter)
        _remove_tmp_dir(tmp_dir)
        await asyncio.to_thread(self._complete_job, ctx, result)

    async def _backfill_date(self, ctx: JobContext, meta: dict[str, Any]) -> None:
        """Fill a missing publish date from yt-dlp metadata (YouTube has none until now)."""
        if ctx.post_published_at is not None:
            return
        published = None
        if meta.get("timestamp"):
            published = datetime.fromtimestamp(meta["timestamp"], tz=UTC)
        elif meta.get("upload_date"):
            try:
                published = datetime.strptime(meta["upload_date"], "%Y%m%d").replace(tzinfo=UTC)
            except ValueError:
                published = None
        if published is None:
            return
        ctx.post_published_at = published

        def _apply() -> None:
            with session_scope(self._factory) as s:
                post = s.get(Post, ctx.post_id)
                if post is not None and post.published_at is None:
                    post.published_at = published
                    if not post.content_html and meta.get("description"):
                        post.content_html = meta["description"]

        await asyncio.to_thread(_apply)

    async def _embed_metadata(
        self, ctx: JobContext, provider, result: DownloadResult, reporter: ProgressReporter
    ) -> DownloadResult:  # noqa: ANN001
        ffmpeg = self.env.resolve_ffmpeg()
        if not ffmpeg:
            return result
        reporter.set_stage("embedding")
        # Pull description/date from the DB; title/creator/thumbnail come from ctx.
        with session_scope(self._factory) as s:
            post = s.get(Post, ctx.post_id)
            description = html_to_text(post.content_html or post.teaser_text) if post else ""
        year = ctx.post_published_at.strftime("%Y-%m-%d") if ctx.post_published_at else ""
        metadata = {
            "title": ctx.post_title or "",
            "artist": ctx.creator_name,
            "album_artist": ctx.creator_name,
            "comment": description,
            "description": description,
            "date": year,
        }
        thumb_path: Path | None = None
        if ctx.post_thumbnail_url:
            thumb_path = result.path.with_name(f".creeparr-cover-{ctx.media_id}.jpg")
            thumb_path = await self._fetch_thumbnail(provider, ctx.post_thumbnail_url, thumb_path)

        def _do() -> DownloadResult:
            changed = embed_metadata(ffmpeg, result.path, metadata, thumb_path)
            if thumb_path and thumb_path.exists():
                thumb_path.unlink(missing_ok=True)
            if not changed:  # ffmpeg failed; the file is untouched and the backlog retries
                return DownloadResult(
                    result.path, result.size, result.sha256, metadata_embedded=False
                )
            size = result.path.stat().st_size
            sha = sha256_file(result.path) if self.settings.get().downloads.compute_sha256 else None
            return DownloadResult(result.path, size, sha, metadata_embedded=True)

        return await self._long(_do)

    async def _fetch_thumbnail(self, provider, url: str, dest: Path) -> Path | None:  # noqa: ANN001
        try:
            resp = await provider.stream(url)
            data = bytearray()
            async for chunk in resp.aiter_bytes(1 << 16):
                data.extend(chunk)
                if len(data) > 8 * 1024 * 1024:
                    break
            await resp.aclose()
            if not data:
                return None
            dest.write_bytes(bytes(data))
            return dest
        except Exception as exc:  # noqa: BLE001
            log.debug("thumbnail fetch failed: %s", exc)
            return None

    async def _refresh_post(self, ctx: JobContext) -> None:
        provider = self.providers.get(ctx.provider)
        pr = await provider.get_post(ctx.campaign_id, ctx.post_ext_id)

        def _apply() -> None:
            with session_scope(self._factory) as s:
                post = s.execute(
                    select(Post)
                    .options(selectinload(Post.media_items))
                    .where(Post.id == ctx.post_id)
                ).scalar_one_or_none()
                if post is None:
                    return
                creator = s.get(Creator, post.creator_id)
                upsert_post(s, creator, pr)
                sync_media_items(s, creator, post, provider.resolve_media(pr))
                for m in post.media_items:
                    if m.id == ctx.media_id:
                        ctx.url = m.source_url
                        ctx.remote_file_name = m.remote_file_name
                        ctx.mimetype = m.mimetype
                        ctx.remote_metadata = m.remote_metadata or {}
                        m.status = MediaStatus.DOWNLOADING
                ctx.post_can_view = post.current_user_can_view
                ctx.post_title = post.title
                ctx.post_urls_fetched_at = post.urls_fetched_at
                recompute_post_status(post)

        await asyncio.to_thread(_apply)


def sync_session(factory: SessionFactory) -> Session:
    return factory()

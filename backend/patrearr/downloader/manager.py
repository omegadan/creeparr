"""Worker pool that turns queued jobs into files on disk."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import random
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from patrearr.config import EnvConfig
from patrearr.core.events import EventBus
from patrearr.core.history import record_event
from patrearr.core.naming import render_template, sanitize_component, split_name, unique_path
from patrearr.core.settings_service import SettingsService
from patrearr.core.state import get_state, set_state
from patrearr.db.engine import SessionFactory, session_scope
from patrearr.db.enums import (
    EventType,
    JobStatus,
    MediaKind,
    MediaSource,
    MediaStatus,
)
from patrearr.db.models import Creator, DownloadJob, MediaItem, Post
from patrearr.downloader.fs import (
    free_space_bytes,
    remove_tree,
    set_times,
    sha256_file,
    try_hardlink,
)
from patrearr.downloader.handlers.base import (
    DownloadCancelled,
    DownloadResult,
    PermanentDownloadError,
    ProgressReporter,
    RetryableDownloadError,
)
from patrearr.downloader.handlers.direct import download_direct
from patrearr.downloader.handlers.ytdlp import YtDlpOptions, normalise_vimeo_url, run_ytdlp
from patrearr.downloader.metadata import embed_metadata, html_to_text
from patrearr.downloader.queue import enqueue_media, publish_post_changed, recompute_post_status
from patrearr.downloader.sidecars import write_nfo, write_text_sidecars
from patrearr.patreon.drm import probe_hls_drm
from patrearr.providers.errors import (
    AuthError,
    CloudflareChallengeError,
    ForbiddenError,
    NotFoundError,
    ProviderError,
)
from patrearr.providers.registry import ProviderRegistry
from patrearr.scanner.scanner import sync_media_items, upsert_post

log = logging.getLogger(__name__)

PAUSED_KEY = "downloads_paused"
TMP_PREFIX = ".patrearr-tmp-"
EMBED_SOURCES = {MediaSource.EMBED_YOUTUBE, MediaSource.EMBED_VIMEO, MediaSource.EMBED_OTHER}


@dataclass
class JobContext:
    job_id: int
    attempt: int
    provider: str
    media_id: int
    post_id: int
    creator_id: int
    media_key: str
    kind: str
    source: str
    url: str | None
    remote_file_name: str | None
    mimetype: str | None
    order_index: int
    remote_metadata: dict[str, Any]
    post_ext_id: str
    post_title: str
    post_published_at: datetime | None
    post_first_seen: datetime | None
    post_type: str | None
    post_thumbnail_url: str | None
    post_sidecars_written: bool
    post_urls_fetched_at: datetime | None
    post_can_view: bool
    creator_name: str
    creator_folder: str | None
    creator_vanity: str | None
    campaign_id: str
    embed_provider: str | None


@dataclass
class RunningJob:
    ctx: JobContext
    reporter: ProgressReporter
    task: asyncio.Task[None] | None = None


class DownloadManager:
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
        self._claim_lock = threading.Lock()

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
            self._check_disk()
            if self.paused:
                await self._sleep(5)
                continue
            try:
                ctx = await asyncio.to_thread(self._claim_next, worker_id)
            except Exception:  # noqa: BLE001
                log.exception("claim failed")
                await self._sleep(5)
                continue
            if ctx is None:
                await self._sleep(5)
                continue
            reporter = ProgressReporter(lambda data, jid=ctx.job_id: self._on_progress(jid, data))
            self._running[ctx.job_id] = RunningJob(ctx, reporter)
            try:
                await self._run_job(ctx, reporter)
            except Exception:  # noqa: BLE001
                log.exception("job %s crashed", ctx.job_id)
                await asyncio.to_thread(
                    self._fail_job, ctx, "internal error", "internal", retryable=True
                )
            finally:
                self._running.pop(ctx.job_id, None)
                self._kick.set()

    async def _sleep(self, seconds: float) -> None:
        self._kick.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._kick.wait(), timeout=seconds)

    # ---- claiming ------------------------------------------------------------------

    def _claim_next(self, worker_id: str) -> JobContext | None:
        with self._claim_lock:
            return self._claim_next_locked(worker_id)

    def _claim_next_locked(self, worker_id: str) -> JobContext | None:
        blocked = self.providers.blocked_names()
        cap = self.settings.get().downloads.max_per_creator
        with session_scope(self._factory) as s:
            running_counts = dict(
                s.execute(
                    select(DownloadJob.creator_id, func.count())
                    .where(DownloadJob.status == JobStatus.RUNNING)
                    .group_by(DownloadJob.creator_id)
                ).all()
            )
            candidates = (
                s.execute(
                    select(DownloadJob)
                    .options(
                        selectinload(DownloadJob.media_item)
                        .selectinload(MediaItem.post)
                        .selectinload(Post.creator)
                    )
                    .where(DownloadJob.status == JobStatus.QUEUED)
                    .order_by(DownloadJob.priority.desc(), DownloadJob.created_at)
                    .limit(200)
                )
                .scalars()
                .all()
            )
            for job in candidates:
                if running_counts.get(job.creator_id, 0) >= cap:
                    continue
                media = job.media_item
                if media is None or media.post is None:
                    continue
                if media.post.creator.provider in blocked and media.source not in EMBED_SOURCES:
                    continue
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

        # 2. Post folder and sidecars.
        post_dir = self.env.download_root(ctx.provider) / self._post_dir(ctx)
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
        try:
            if ctx.source in (MediaSource.NATIVE_DIRECT, MediaSource.MEDIA_DOWNLOAD):
                ext = self._guess_ext(ctx)
                dest = unique_path(post_dir / self._file_name(ctx, ext))
                reporter.set_stage("downloading")
                result = await download_direct(
                    provider,
                    ctx.url,
                    dest,
                    reporter,
                    compute_sha256=settings.downloads.compute_sha256,
                )
            else:
                url = ctx.url
                if ctx.source == MediaSource.EMBED_VIMEO:
                    url = normalise_vimeo_url(url)
                opts = YtDlpOptions(
                    headers=provider.media_headers(),
                    cookiefile=str(provider.cookie_file) if provider.cookie_file.exists() else None,
                    video_format=settings.downloads.video_format,
                    ffmpeg_location=self.env.resolve_ffmpeg(),
                    fragment_concurrency=settings.downloads.hls_fragment_concurrency,
                    impersonate=provider.ytdlp_impersonate,
                    remote_components=settings.downloads.ytdlp_remote_components,
                )
                reporter.set_stage("downloading")
                produced, yt_meta = await asyncio.to_thread(
                    run_ytdlp, url, tmp_dir, opts, reporter, label
                )
                await self._backfill_date(ctx, yt_meta)
                reporter.set_stage("verifying")
                result = await asyncio.to_thread(
                    self._finalise_ytdlp, ctx, post_dir, produced, settings.downloads.compute_sha256
                )
        except DownloadCancelled:
            remove_tree(tmp_dir)
            await asyncio.to_thread(self._cancel_job_db, ctx)
            return
        except PermanentDownloadError as exc:
            remove_tree(tmp_dir)
            await asyncio.to_thread(self._fail_job, ctx, str(exc), exc.error_class, retryable=False)
            return
        except RetryableDownloadError as exc:
            await asyncio.to_thread(self._fail_job, ctx, str(exc), exc.error_class, retryable=True)
            return
        if ctx.kind == MediaKind.VIDEO and settings.naming.embed_metadata:
            result = await self._embed_metadata(ctx, provider, result, reporter)
        remove_tree(tmp_dir)
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
            thumb_path = result.path.with_name(".patrearr-cover.jpg")
            thumb_path = await self._fetch_thumbnail(provider, ctx.post_thumbnail_url, thumb_path)

        def _do() -> DownloadResult:
            changed = embed_metadata(ffmpeg, result.path, metadata, thumb_path)
            if thumb_path and thumb_path.exists():
                thumb_path.unlink(missing_ok=True)
            if not changed:
                return DownloadResult(
                    result.path, result.size, result.sha256, metadata_embedded=True
                )
            size = result.path.stat().st_size
            sha = sha256_file(result.path) if self.settings.get().downloads.compute_sha256 else None
            return DownloadResult(result.path, size, sha, metadata_embedded=True)

        return await asyncio.to_thread(_do)

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
                provider, thumb_url, abs_path.with_name(".patrearr-cover.jpg")
            )

        def _do() -> tuple[int, str | None]:
            embed_metadata(ffmpeg, abs_path, metadata, thumb_path)
            if thumb_path and thumb_path.exists():
                thumb_path.unlink(missing_ok=True)
            size = abs_path.stat().st_size
            sha = sha256_file(abs_path) if self.settings.get().downloads.compute_sha256 else None
            return size, sha

        size, sha = await asyncio.to_thread(_do)
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

    # ---- naming --------------------------------------------------------------------

    def _values(self, ctx: JobContext) -> dict[str, Any]:
        return {
            "creator": ctx.creator_folder or ctx.creator_name,
            "creator_vanity": ctx.creator_vanity or ctx.campaign_id,
            "campaign_id": ctx.campaign_id,
            "title": ctx.post_title or "untitled",
            "post_id": ctx.post_ext_id,
            "published": (
                ctx.post_published_at or ctx.post_first_seen or datetime(1970, 1, 1, tzinfo=UTC)
            ),
            "post_type": ctx.post_type or "",
            "media_kind": ctx.kind,
            "media_index": ctx.order_index,
            "embed_provider": ctx.embed_provider or "",
        }

    def _post_dir(self, ctx: JobContext) -> PurePosixPath:
        naming = self.settings.get().naming
        return render_template(
            naming.post_folder_template, self._values(ctx), naming.max_component_length
        )

    def _guess_ext(self, ctx: JobContext) -> str:
        _, ext = split_name(ctx.remote_file_name)
        if ext and ext != "m3u8":
            return ext
        if ctx.url:
            _, ext = split_name(PurePosixPath(ctx.url.split("?", 1)[0]).name)
            if ext and len(ext) <= 5 and ext != "m3u8":
                return ext
        if ctx.mimetype:
            guess = mimetypes.guess_extension(ctx.mimetype.split(";")[0].strip())
            if guess:
                return guess.lstrip(".")
        return {"video": "mp4", "audio": "mp3", "image": "jpg"}.get(ctx.kind, "bin")

    def _file_name(self, ctx: JobContext, ext: str) -> str:
        naming = self.settings.get().naming
        stem, _ = split_name(ctx.remote_file_name)
        if not stem or ctx.source in EMBED_SOURCES or ctx.source == MediaSource.NATIVE_HLS:
            stem = ctx.post_title or "untitled"
            if ctx.kind != MediaKind.VIDEO or ctx.order_index > 1:
                stem = f"{stem} - {ctx.kind} {ctx.order_index}"
        filename = f"{stem}.{ext}" if ext else stem
        values = {**self._values(ctx), "filename": filename, "ext": ext}
        rendered = render_template(naming.file_template, values, naming.max_component_length)
        return str(rendered) if len(rendered.parts) == 1 else sanitize_component(filename)

    def _finalise_ytdlp(
        self, ctx: JobContext, post_dir: Path, produced: Path, compute_sha256: bool
    ) -> DownloadResult:
        from patrearr.downloader.fs import atomic_replace, sha256_file

        ext = produced.suffix.lstrip(".").lower() or "mp4"
        dest = unique_path(post_dir / self._file_name(ctx, ext))
        atomic_replace(produced, dest)
        size = dest.stat().st_size
        sha = sha256_file(dest) if compute_sha256 else None
        return DownloadResult(dest, size, sha)

    # ---- DB transitions ------------------------------------------------------------

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
            with session_scope(self._factory) as s:
                job = s.get(DownloadJob, job_id)
                if job is None:
                    return
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
                select(MediaItem)
                .where(
                    MediaItem.sha256 == result.sha256,
                    MediaItem.id != ctx.media_id,
                    MediaItem.status == MediaStatus.COMPLETED,
                    MediaItem.file_path.is_not(None),
                )
                .limit(1)
            ).scalar_one_or_none()
            twin_rel = twin.file_path if twin else None
        if not twin_rel:
            return False
        for root in self.env.download_roots().values():
            source = root / twin_rel
            if source.exists() and try_hardlink(result.path, source):
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
            media = s.execute(
                select(MediaItem)
                .options(selectinload(MediaItem.post).selectinload(Post.media_items))
                .where(MediaItem.id == ctx.media_id)
            ).scalar_one()
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
            media = s.execute(
                select(MediaItem)
                .options(selectinload(MediaItem.post).selectinload(Post.media_items))
                .where(MediaItem.id == ctx.media_id)
            ).scalar_one()
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
            media = s.execute(
                select(MediaItem)
                .options(selectinload(MediaItem.post).selectinload(Post.media_items))
                .where(MediaItem.id == ctx.media_id)
            ).scalar_one()
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
                job.started_at = None
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


def sync_session(factory: SessionFactory) -> Session:
    return factory()

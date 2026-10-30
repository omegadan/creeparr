"""The download run: turns a claimed download into an outcome.

It renews the post's links when they may have expired, checks for DRM, has a handler bring
the file to disk, files it in the library and finishes it: embedded metadata, dedupe, NFO
and the post's date on the file. It changes no statuses; the worker hands the outcome to the
media lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from creeparr.config import EnvConfig
from creeparr.core.downloads import (
    INTERNAL_ERROR,
    Cancelled,
    Claim,
    Completed,
    Failed,
    FailureKind,
    Outcome,
)
from creeparr.core.lifecycle import MediaLifecycle
from creeparr.core.settings_service import SettingsService
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import EMBED_SOURCES, MediaKind, MediaSource, MediaStatus
from creeparr.db.models import Creator, MediaItem, Post
from creeparr.downloader.embed import Embedder
from creeparr.downloader.fs import set_times, sha256_file, try_hardlink
from creeparr.downloader.handlers.base import (
    DownloadCancelled,
    DownloadFailed,
    Handler,
    MediaSnapshot,
    ProgressReporter,
)
from creeparr.downloader.longwork import LongWork
from creeparr.downloader.placement import PostPlacement, Reservations, sweep_scratch
from creeparr.downloader.sidecars import write_nfo
from creeparr.patreon.drm import probe_hls_drm
from creeparr.providers.base import ProviderService
from creeparr.providers.errors import (
    AuthError,
    CloudflareChallengeError,
    ForbiddenError,
    NotFoundError,
    ProviderError,
)
from creeparr.providers.registry import ProviderRegistry

log = logging.getLogger(__name__)

# A provider's own files, fetched over plain HTTP; everything else goes through yt-dlp.
_DIRECT_SOURCES = {MediaSource.NATIVE_DIRECT, MediaSource.MEDIA_DOWNLOAD}


class DownloadRun:
    def __init__(
        self,
        env: EnvConfig,
        session_factory: SessionFactory,
        settings: SettingsService,
        providers: ProviderRegistry,
        lifecycle: MediaLifecycle,
        *,
        direct: Handler,
        ytdlp: Handler,
        embedder: Embedder,
        long_work: LongWork,
    ) -> None:
        self.env = env
        self._factory = session_factory
        self.settings = settings
        self.providers = providers
        self.lifecycle = lifecycle
        self._direct = direct
        self._ytdlp = ytdlp
        self._embedder = embedder
        self._long = long_work
        self._reservations = Reservations()

    async def run(self, claim: Claim, progress: ProgressReporter) -> Outcome:
        """Download the claimed media item and say how it went.

        Never raises: an unexpected error becomes a retryable internal failure. Only a
        shutdown's `asyncio.CancelledError` gets through; the download then stays running
        until the next start puts it back in the queue. Downloads of different claims may
        run at once. `progress` carries the cancel flag, which may be set from any thread.
        """
        try:
            return await self._run(claim, progress)
        except Exception:  # noqa: BLE001
            log.exception("job %s crashed", claim.job_id)
            return INTERNAL_ERROR
        finally:
            self._reservations.release(claim.job_id)

    def sweep(self) -> None:
        """Delete scratch folders that interrupted downloads left. Call it only while no run
        is in progress (at startup), or it deletes live ones. Blocking."""
        sweep_scratch(self.env.download_roots().values())

    async def _run(self, claim: Claim, progress: ProgressReporter) -> Outcome:
        settings = self.settings.get()
        media = await asyncio.to_thread(self._load, claim)
        if media is None:
            return Cancelled()  # the media item is gone
        provider = self.providers.get(media.provider)
        log.info("%s starting %s %s", media.label, media.source, media.post_title)

        # 1. Renew the post's signed links when they may have expired (native media only).
        if media.source not in EMBED_SOURCES:
            max_age = timedelta(minutes=settings.downloads.url_max_age_minutes)
            stale = media.urls_fetched_at is None or (
                datetime.now(UTC) - media.urls_fetched_at > max_age
            )
            if stale or claim.attempt > 1 or not media.url:
                renewed = await self._renew_links(claim, media, provider)
                if not isinstance(renewed, MediaSnapshot):
                    return renewed
                media = renewed
            if not media.can_view:
                return Failed(FailureKind.NO_ACCESS, "post not accessible", "no_access")
        if not media.url:
            return Failed(FailureKind.RETRYABLE, "no download URL", "no_url")

        # 2. The post's folder and sidecars. A handler that learns an undated post's date
        # (yt-dlp) has them made once it knows, so the folder is named after that date.
        handler = self._direct if media.source in _DIRECT_SOURCES else self._ytdlp
        root = self.env.download_root(media.provider)
        place = PostPlacement(
            root=root,
            media=media,
            naming=settings.naming,
            reservations=self._reservations,
            session_factory=self._factory,
            date_pending=media.published_at is None and handler.learns_date,
        )
        if not place.date_pending:
            await asyncio.to_thread(place.commit_folder)

        # 3. DRM: flagged by the provider up front, or probed from the HLS playlist.
        if media.remote_metadata.get("drm"):
            return Failed(FailureKind.DRM, "DRM-protected media", "drm")
        if media.source == MediaSource.NATIVE_HLS:
            try:
                drm = await probe_hls_drm(provider.fetch_text, media.url)
            except ProviderError as exc:
                return Failed(FailureKind.RETRYABLE, f"HLS probe failed: {exc}", "hls_probe")
            if drm:
                return Failed(FailureKind.DRM, "DRM-protected stream", "drm")

        # 4. Download, then file a scratch download in the post's folder.
        try:
            fetched = await handler.fetch(media, place, progress)
            path = fetched.path
            if not fetched.placed:
                progress.set_stage("verifying")
                path = await asyncio.to_thread(place.file, fetched.path, fetched.learned)
        except DownloadCancelled:
            place.remove_scratch()
            return Cancelled()
        except DownloadFailed as exc:
            if not exc.kind.retried:
                place.remove_scratch()  # a retry would resume from it
            return Failed(exc.kind, str(exc), exc.label)
        media = place.media  # with the date it learned
        sha = fetched.sha256
        if sha is None and settings.downloads.compute_sha256:
            sha = await self._long.run(sha256_file, path)
        size = path.stat().st_size

        # 5. Finish the file. Embedding rewrites the video, so dedupe looks at its new hash.
        embedded = False
        if media.kind == MediaKind.VIDEO and settings.naming.embed_metadata:
            result = await self._embedder.embed(media.media_id, path, progress)
            if result is not None:
                size, sha, embedded = result.size, result.sha256, True
        place.remove_scratch()
        await asyncio.to_thread(self._finish, media, path, sha)
        return Completed(str(path.relative_to(root)), size, sha, embedded)

    async def _renew_links(
        self, claim: Claim, media: MediaSnapshot, provider: ProviderService
    ) -> MediaSnapshot | Outcome:
        """Fetch the post again and sync it, for fresh signed links. Returns the media as it
        is now, or the outcome when that failed or the media item is gone."""
        try:
            pr = await provider.get_post(media.campaign_id, media.post_ext_id)
        except (AuthError, CloudflareChallengeError) as exc:
            provider.mark_auth_invalid(exc)
            return Failed(FailureKind.AUTH, f"auth: {exc}", "auth")
        except ForbiddenError as exc:
            return Failed(FailureKind.NO_ACCESS, str(exc), "no_access")
        except NotFoundError as exc:
            return Failed(FailureKind.POST_GONE, f"post gone: {exc}", "not_found")
        except ProviderError as exc:
            return Failed(FailureKind.RETRYABLE, str(exc), "api")

        def _apply() -> MediaSnapshot | None:
            with session_scope(self._factory) as s:
                post = s.get(Post, media.post_id)
                creator = s.get(Creator, media.creator_id)
                if post is None or creator is None:
                    return None
                self.lifecycle.sync_post(s, creator, pr, provider.resolve_media, refresh=True)
                return _snapshot(s, claim)

        renewed = await asyncio.to_thread(_apply)
        return renewed if renewed is not None else Cancelled()

    def _load(self, claim: Claim) -> MediaSnapshot | None:
        with session_scope(self._factory) as s:
            return _snapshot(s, claim)

    def _finish(self, media: MediaSnapshot, path: Path, sha: str | None) -> None:
        settings = self.settings.get()
        if sha and settings.downloads.deduplicate:
            self._dedupe(media, path, sha)
        if media.kind == MediaKind.VIDEO and settings.naming.write_nfo:
            self._write_nfo(media, path)
        # Stamp the file and its post folder with the post's publish date.
        if media.published_at:
            set_times(path, media.published_at)
            set_times(path.parent, media.published_at)

    def _dedupe(self, media: MediaSnapshot, path: Path, sha: str) -> None:
        """If another archived file has the same content, hardlink to it to save disk."""
        with session_scope(self._factory) as s:
            twin = s.execute(
                select(MediaItem.file_path, Creator.provider)
                .join(Creator, Creator.id == MediaItem.creator_id)
                .where(
                    MediaItem.sha256 == sha,
                    MediaItem.id != media.media_id,
                    MediaItem.status == MediaStatus.COMPLETED,
                    MediaItem.file_path.is_not(None),
                )
                .limit(1)
            ).first()
        if twin is None:
            return
        # The twin's path is relative to its own provider's root, not any root.
        source = self.env.download_root(twin.provider) / twin.file_path
        if source.is_file() and try_hardlink(path, source):
            log.info("%s deduplicated (sha %s)", media.label, sha[:12])

    def _write_nfo(self, media: MediaSnapshot, path: Path) -> None:
        with session_scope(self._factory) as s:
            post = s.get(Post, media.post_id)
            creator = s.get(Creator, media.creator_id)
            if post and creator:
                try:
                    write_nfo(path.parent, post, creator, path.name)
                except OSError as exc:
                    log.debug("nfo write failed: %s", exc)


def _snapshot(session: Session, claim: Claim) -> MediaSnapshot | None:
    item = session.execute(
        select(MediaItem)
        .options(selectinload(MediaItem.post).selectinload(Post.creator))
        .where(MediaItem.id == claim.media_id)
    ).scalar_one_or_none()
    if item is None or item.post is None or item.post.creator is None:
        return None
    post, creator = item.post, item.post.creator
    return MediaSnapshot(
        job_id=claim.job_id,
        attempt=claim.attempt,
        provider=creator.provider,
        media_id=item.id,
        kind=item.kind,
        source=item.source,
        url=item.source_url,
        remote_file_name=item.remote_file_name,
        mimetype=item.mimetype,
        order_index=item.order_index,
        remote_metadata=dict(item.remote_metadata or {}),
        post_id=post.id,
        post_ext_id=post.post_id,
        post_title=post.title,
        published_at=post.published_at,
        first_seen_at=post.first_seen_at,
        post_type=post.post_type,
        embed_provider=post.embed_provider,
        urls_fetched_at=post.urls_fetched_at,
        can_view=post.current_user_can_view,
        creator_id=creator.id,
        creator_name=creator.name,
        creator_folder=creator.folder_name,
        creator_vanity=creator.vanity,
        campaign_id=creator.campaign_id,
    )

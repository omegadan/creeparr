"""The download pieces wired for tests, with fakes at their seams (handlers, ffmpeg, yt-dlp)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from creeparr.core.lifecycle import MediaLifecycle
from creeparr.db.engine import session_scope
from creeparr.db.enums import AuthState
from creeparr.db.models import Creator, DownloadJob
from creeparr.downloader.claims import Claims
from creeparr.downloader.embed import Embedder
from creeparr.downloader.handlers.base import (
    Fetched,
    Handler,
    Learned,
    MediaSnapshot,
    Placement,
    ProgressReporter,
)
from creeparr.downloader.handlers.direct import DirectHandler
from creeparr.downloader.handlers.ytdlp import YtDlpEngine, YtDlpHandler
from creeparr.downloader.library import Library
from creeparr.downloader.longwork import LongWork
from creeparr.downloader.manager import DownloadManager
from creeparr.downloader.progress import JobProgress
from creeparr.downloader.run import DownloadRun
from creeparr.patreon.media_resolver import resolve_media
from creeparr.patreon.parsing import IncludedIndex, post_from_resource
from creeparr.providers.patreon import PatreonProvider
from creeparr.providers.registry import ProviderRegistry
from tests import patreon_fixtures as fx


class FakeFfmpeg:
    """Stands in for the ffmpeg program. `location=None` means it isn't installed."""

    def __init__(
        self, location: str | None = None, *, embeds: bool = True, remuxes: bool = True
    ) -> None:
        self.location = location
        self.embeds = embeds
        self.remuxes = remuxes
        #: (video name, tags, cover bytes) per embed call
        self.embedded: list[tuple[str, dict[str, str], bytes | None]] = []

    def remux(self, src: Path, dst: Path) -> bool:
        if not self.remuxes:
            return False
        dst.write_bytes(src.read_bytes())
        return True

    def embed(self, video: Path, metadata: dict[str, str], cover: Path | None) -> bool:
        self.embedded.append((video.name, dict(metadata), cover.read_bytes() if cover else None))
        if not self.embeds:
            return False
        with video.open("ab") as f:
            f.write(b"+tags")
        return True


Hook = Callable[[MediaSnapshot, Placement, ProgressReporter], Awaitable[None]]


class FakeHandler:
    """Writes `data` as the media's file: at a reserved name, or, like yt-dlp
    (`learns_date`), into the scratch folder with what it learned. `before` runs first and
    may raise; `error` is raised instead of writing."""

    def __init__(
        self,
        data: bytes = b"file",
        ext: str = "mp4",
        *,
        learns_date: bool = False,
        learned: Learned | None = None,
        error: Exception | None = None,
        before: Hook | None = None,
    ) -> None:
        self.data = data
        self.ext = ext
        self.learns_date = learns_date
        self.learned = learned
        self.error = error
        self.before = before
        self.calls: list[MediaSnapshot] = []

    async def fetch(
        self, media: MediaSnapshot, place: Placement, progress: ProgressReporter
    ) -> Fetched:
        self.calls.append(media)
        if self.before is not None:
            await self.before(media, place, progress)
        if self.error is not None:
            raise self.error
        if self.learns_date:
            path = place.scratch() / f"media.{self.ext}"
            path.write_bytes(self.data)
            return Fetched(path, placed=False, learned=self.learned)
        path = place.reserve(self.ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.data)
        return Fetched(path)


def fake_ytdlp(meta: dict[str, Any] | None = None, *, data: bytes = b"\x1a\x45\xdf\xa3"):
    """A yt-dlp engine that 'downloads' `data` and reports `meta`; it records its thread."""
    threads: list[str] = []

    def engine(url, tmp_dir, opts, reporter, label):  # noqa: ANN001, ANN202
        import threading

        threads.append(threading.current_thread().name)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        produced = tmp_dir / f"media.{opts.container}"
        produced.write_bytes(data)
        return produced, dict(meta or {})

    engine.threads = threads  # type: ignore[attr-defined]
    return engine


@dataclass
class Kit:
    lifecycle: MediaLifecycle
    claims: Claims
    run: DownloadRun
    manager: DownloadManager
    library: Library
    ffmpeg: FakeFfmpeg


def build(
    env,
    session_factory,
    settings,
    bus,
    providers: ProviderRegistry,
    *,
    direct: Handler | None = None,
    ytdlp: Handler | None = None,
    engine: YtDlpEngine | None = None,
    ffmpeg: FakeFfmpeg | None = None,
    free_space: Callable[[Path], int] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Kit:
    """Everything services.py builds for downloads, with fakes where given. ffmpeg defaults
    to a fake that isn't installed, so no test runs the machine's real ffmpeg."""
    long_work = LongWork(4)
    ffmpeg = ffmpeg or FakeFfmpeg()
    lifecycle = MediaLifecycle(session_factory, bus, settings)
    embedder = Embedder(session_factory, settings, providers, ffmpeg, long_work)
    claims = Claims(
        session_factory, settings, providers, lifecycle, **({"clock": clock} if clock else {})
    )
    if ytdlp is None:
        extra = {"engine": engine} if engine else {}
        ytdlp = YtDlpHandler(settings, providers, ffmpeg, long_work, **extra)
    run = DownloadRun(
        env,
        session_factory,
        settings,
        providers,
        lifecycle,
        direct=direct or DirectHandler(settings, providers, ffmpeg, long_work),
        ytdlp=ytdlp,
        embedder=embedder,
        long_work=long_work,
    )
    manager = DownloadManager(
        env,
        session_factory,
        settings,
        bus,
        lifecycle,
        claims,
        run,
        JobProgress(session_factory, bus),
        **({"free_space": free_space} if free_space else {}),
    )
    lifecycle.workers = manager
    library = Library(env, session_factory, settings, bus, lifecycle, embedder)
    return Kit(lifecycle, claims, run, manager, library, ffmpeg)


def patreon_registry(env, settings, session_factory, bus) -> ProviderRegistry:
    settings.update(
        {"patreon": {"session_id": "sid-123", "requests_per_second": 10}}, allow_secrets=True
    )
    registry = ProviderRegistry([PatreonProvider(env, settings, session_factory, bus)])
    registry.get("patreon")._set_auth_status(AuthState.VALID, user_name="Test Patron")
    return registry


def add_creator(session_factory, **kw: Any) -> int:
    kw = {"campaign_id": fx.CAMPAIGN_ID, "name": "Example Creator", "vanity": fx.VANITY, **kw}
    with session_scope(session_factory) as s:
        creator = Creator(**kw)
        s.add(creator)
        s.flush()
        return creator.id


def queue_post(
    session_factory, lifecycle: MediaLifecycle, resource: dict, creator_id: int | None = None
) -> tuple[int, int | None]:
    """Sync a post the way a scan does, which queues its wanted media. Returns the post id
    and its first job's id (creating the creator on first use)."""
    pr = post_from_resource(resource, IncludedIndex(fx.posts_page([resource])))
    with session_scope(session_factory) as s:
        if creator_id is None:
            creator = s.execute(select(Creator)).scalars().first()
            if creator is None:
                creator = Creator(
                    campaign_id=fx.CAMPAIGN_ID, name="Example Creator", vanity=fx.VANITY
                )
                s.add(creator)
                s.flush()
        else:
            creator = s.get(Creator, creator_id)
        post = lifecycle.sync_post(s, creator, pr, resolve_media).post
        job = (
            s.execute(
                select(DownloadJob).where(DownloadJob.post_id == post.id).order_by(DownloadJob.id)
            )
            .scalars()
            .first()
        )
        return post.id, job.id if job else None


def job(session_factory, job_id: int) -> DownloadJob:
    with session_scope(session_factory) as s:
        row = s.get(DownloadJob, job_id)
        s.expunge(row)
        return row


async def wait_for(pred: Callable[[], bool], timeout: float = 10.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False

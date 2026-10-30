"""The download run, through its interface: a claim in, an outcome out, files on disk."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from yt_dlp.utils import DownloadError, UnsupportedError

from creeparr.config import EnvConfig
from creeparr.core.downloads import (
    INTERNAL_ERROR,
    Cancelled,
    Completed,
    Failed,
    FailureKind,
    Outcome,
)
from creeparr.core.events import EventBus
from creeparr.core.settings_service import SettingsService
from creeparr.db.engine import make_engine, make_session_factory, session_scope
from creeparr.db.enums import MediaStatus
from creeparr.db.migrate import run_migrations
from creeparr.db.models import Creator, MediaItem, Post
from creeparr.downloader.handlers.base import DownloadCancelled, DownloadFailed, ProgressReporter
from creeparr.downloader.handlers.ytdlp import classify_ytdlp_error
from creeparr.downloader.placement import Reservations
from creeparr.patreon.client import API_URL
from tests import patreon_fixtures as fx
from tests.conftest import json_response
from tests.downloads import (
    FakeFfmpeg,
    FakeHandler,
    Kit,
    add_creator,
    build,
    fake_ytdlp,
    patreon_registry,
    queue_post,
)

VIDEO_URL = f"{fx.CDN}/p1/episode.mp4?token=x"
EPISODE = Path("Example Creator") / "2026-03-14 - Ep 1 [p1]" / "episode.mp4"


@pytest.fixture
async def providers(env, settings, session_factory, bus):
    registry = patreon_registry(env, settings, session_factory, bus)
    yield registry
    await registry.aclose_all()


async def run_next(kit: Kit) -> Outcome:
    claim = await asyncio.to_thread(kit.claims.next, "w0")
    assert claim is not None
    return await kit.run.run(claim, ProgressReporter(lambda _data: None))


def media_row(session_factory) -> MediaItem:
    with session_scope(session_factory) as s:
        item = s.execute(select(MediaItem)).scalar_one()
        s.expunge(item)
        return item


@pytest.mark.asyncio
async def test_an_undated_video_is_filed_under_the_date_yt_dlp_learns(
    env, session_factory, settings, bus, providers
):
    # A YouTube back-catalogue video has no date until yt-dlp reports one. The folder
    # used to be named from the first-seen date because it was picked before that.
    engine = fake_ytdlp({"upload_date": "20190504", "description": "About this video"})
    kit = build(env, session_factory, settings, bus, providers, engine=engine)
    post_id, _ = queue_post(
        session_factory, kit.lifecycle, fx.youtube_post("y1", title="Old video", content="")
    )
    with session_scope(session_factory) as s:
        s.get(Post, post_id).published_at = None

    outcome = await run_next(kit)

    folder = Path("Example Creator") / "2019-05-04 - Old video [y1]"
    assert isinstance(outcome, Completed) and Path(outcome.file_path).parent == folder
    creator_dir = env.download_dir / "Example Creator"
    assert [p.name for p in creator_dir.iterdir()] == [folder.name]  # no first-seen folder
    assert (env.download_dir / folder / "post.json").exists()
    with session_scope(session_factory) as s:
        post = s.get(Post, post_id)
        assert post.published_at == datetime(2019, 5, 4, tzinfo=UTC)
        assert post.content_html == "About this video"
        assert post.folder_path == str(folder)
    assert media_row(session_factory).status == MediaStatus.DOWNLOADING  # the run records nothing


@pytest.mark.asyncio
async def test_a_retryable_failure_keeps_the_part_file_for_a_resume(
    env, session_factory, settings, bus, providers, respx_mock
):
    respx_mock.get(VIDEO_URL).mock(return_value=httpx.Response(503))
    kit = build(env, session_factory, settings, bus, providers)
    queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1", title="Ep 1"))
    part = env.download_dir / EPISODE.with_name("episode.mp4.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"half a video")  # left by an earlier attempt

    outcome = await run_next(kit)

    assert outcome == Failed(FailureKind.RETRYABLE, "HTTP 503", "server")
    assert part.read_bytes() == b"half a video"


@pytest.mark.asyncio
async def test_a_permanent_failure_removes_the_part_file(
    env, session_factory, settings, bus, providers, respx_mock
):
    respx_mock.get(VIDEO_URL).mock(return_value=httpx.Response(404))
    kit = build(env, session_factory, settings, bus, providers)
    queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1", title="Ep 1"))
    part = env.download_dir / EPISODE.with_name("episode.mp4.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"half a video")

    outcome = await run_next(kit)

    assert outcome == Failed(FailureKind.FILE_GONE, "HTTP 404: file gone", "not_found")
    assert not part.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kept"),
    [
        (DownloadFailed(FailureKind.RETRYABLE, "fragment 7 failed", "ytdlp"), True),
        (DownloadFailed(FailureKind.UNAVAILABLE, "Private video", "unavailable"), False),
        (DownloadCancelled(), False),
    ],
)
async def test_the_scratch_folder_is_kept_only_for_a_retry(
    env, session_factory, settings, bus, providers, error, kept
):
    async def partly_downloaded(media, place, progress) -> None:
        (place.scratch() / "media.f137.mp4.part").write_bytes(b"partial")

    handler = FakeHandler(learns_date=True, before=partly_downloaded, error=error)
    kit = build(env, session_factory, settings, bus, providers, ytdlp=handler)
    queue_post(session_factory, kit.lifecycle, fx.youtube_post("y1", title="Clip"))

    outcome = await run_next(kit)

    assert isinstance(outcome, Cancelled if isinstance(error, DownloadCancelled) else Failed)
    assert bool(list(env.download_dir.rglob(".creeparr-tmp-*"))) == kept


@pytest.mark.asyncio
async def test_a_failed_embed_is_left_for_the_backlog(
    env, session_factory, settings, bus, providers, respx_mock
):
    # When ffmpeg can't embed metadata the file is left untouched; it must not be
    # recorded as embedded, or the embed backlog never tries it again.
    respx_mock.get(f"{fx.CDN}/y1/thumb_large.jpg").mock(return_value=httpx.Response(200))
    ffmpeg = FakeFfmpeg("ffmpeg", embeds=False)
    kit = build(env, session_factory, settings, bus, providers, engine=fake_ytdlp(), ffmpeg=ffmpeg)
    queue_post(session_factory, kit.lifecycle, fx.youtube_post("y1", title="Clip"))

    outcome = await run_next(kit)

    assert isinstance(outcome, Completed) and outcome.metadata_embedded is False
    assert (env.download_dir / outcome.file_path).read_bytes() == b"\x1a\x45\xdf\xa3"
    assert ffmpeg.embedded  # it was tried


@pytest.mark.asyncio
async def test_embedding_uses_the_post_as_it_is_now(
    env, session_factory, settings, bus, providers, respx_mock
):
    # A download that renewed its post's links must embed the renewed cover link: the
    # one it was claimed with may have expired by then.
    renewed_cover = f"{fx.CDN}/y1/thumb.jpg?token=renewed"
    cover = respx_mock.get(renewed_cover).mock(return_value=httpx.Response(200, content=b"jpg"))
    ffmpeg = FakeFfmpeg("ffmpeg")
    kit = build(env, session_factory, settings, bus, providers, engine=fake_ytdlp(), ffmpeg=ffmpeg)
    post_id, _ = queue_post(session_factory, kit.lifecycle, fx.youtube_post("y1", title="Clip"))
    claim = await asyncio.to_thread(kit.claims.next, "w0")
    with session_scope(session_factory) as s:
        s.get(Post, post_id).thumbnail_url = renewed_cover

    outcome = await kit.run.run(claim, ProgressReporter(lambda _data: None))

    assert cover.called
    ((_video, tags, cover_bytes),) = ffmpeg.embedded
    assert cover_bytes == b"jpg" and tags["title"] == "Clip" and tags["date"] == "2026-03-14"
    data = (env.download_dir / outcome.file_path).read_bytes()
    assert outcome == Completed(
        outcome.file_path, len(data), hashlib.sha256(data).hexdigest(), metadata_embedded=True
    )


@pytest.mark.asyncio
async def test_drm_flagged_by_the_provider_fails_without_downloading(
    env, session_factory, settings, bus, providers
):
    handler = FakeHandler()
    kit = build(env, session_factory, settings, bus, providers, direct=handler)
    queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1"))
    with session_scope(session_factory) as s:
        s.execute(select(MediaItem)).scalar_one().remote_metadata = {"drm": True}

    assert await run_next(kit) == Failed(FailureKind.DRM, "DRM-protected media", "drm")
    assert not handler.calls


@pytest.mark.parametrize(
    ("error", "kind", "label"),
    [
        (DownloadError("ERROR: [vimeo] 12: This video is DRM protected"), FailureKind.DRM, "drm"),
        (
            DownloadError("ERROR: [youtube] x: Private video"),
            FailureKind.UNAVAILABLE,
            "unavailable",
        ),
        (
            DownloadError("ERROR: Sign in to confirm you're not a bot"),
            FailureKind.NEEDS_COOKIES,
            "needs_cookies",
        ),
        (UnsupportedError("https://example.com/x"), FailureKind.UNSUPPORTED, "unsupported"),
        (DownloadError("ERROR: fragment 3 not found"), FailureKind.RETRYABLE, "ytdlp"),
    ],
)
def test_yt_dlp_errors_are_classified(error, kind, label):
    failure = classify_ytdlp_error(error)
    assert (failure.kind, failure.label) == (kind, label)


@pytest.mark.asyncio
async def test_a_post_gone_while_renewing_links_fails_permanently(
    env, session_factory, settings, bus, providers, respx_mock
):
    respx_mock.get(f"{API_URL}/posts/p1").mock(return_value=httpx.Response(404))
    kit = build(env, session_factory, settings, bus, providers)
    post_id, _ = queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1"))
    with session_scope(session_factory) as s:
        s.get(Post, post_id).urls_fetched_at = None  # stale: renew first

    outcome = await run_next(kit)

    assert isinstance(outcome, Failed)
    assert (outcome.kind, outcome.label) == (FailureKind.POST_GONE, "not_found")
    assert outcome.message.startswith("post gone")


@pytest.mark.asyncio
async def test_media_the_renewed_post_no_longer_has_is_cancelled(
    env, session_factory, settings, bus, providers, respx_mock
):
    edited = fx.post_resource("p1", post_type="text_only", post_file=None)
    respx_mock.get(f"{API_URL}/posts/p1").mock(return_value=json_response(fx.post_response(edited)))
    handler = FakeHandler()
    kit = build(env, session_factory, settings, bus, providers, direct=handler)
    post_id, _ = queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1"))
    with session_scope(session_factory) as s:
        s.get(Post, post_id).urls_fetched_at = None

    assert await run_next(kit) == Cancelled()
    assert not handler.calls


@pytest.mark.asyncio
async def test_an_unexpected_error_becomes_a_retryable_internal_failure(
    env, session_factory, settings, bus, providers
):
    kit = build(
        env, session_factory, settings, bus, providers, direct=FakeHandler(error=RuntimeError("x"))
    )
    queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1"))

    assert await run_next(kit) == INTERNAL_ERROR


@pytest.mark.asyncio
async def test_dedupe_links_to_the_twin_in_its_own_providers_root(tmp_path):
    # A twin's file_path is relative to its provider's root. Trying every root could
    # hardlink to an unrelated file that happens to sit at the same relative path.
    env = EnvConfig(
        config_dir=tmp_path / "config",
        download_dir=tmp_path / "downloads",
        youtube_download_dir=tmp_path / "yt",
    )
    env.ensure_dirs()
    run_migrations(env.database_url)
    factory = make_session_factory(make_engine(env.database_url))
    settings, bus = SettingsService(factory), EventBus()
    providers = patreon_registry(env, settings, factory, bus)
    rel = "Chan/clip.mkv"
    for root, data in ((env.download_dir, b"decoy"), (tmp_path / "yt", b"same")):
        (root / rel).parent.mkdir(parents=True)
        (root / rel).write_bytes(data)
    patreon_creator = add_creator(factory)
    with session_scope(factory) as s:
        c = Creator(campaign_id="UC1", name="Chan", provider="youtube")
        s.add(c)
        s.flush()
        post = Post(post_id="v1", creator_id=c.id, title="t", first_seen_at=datetime.now(UTC))
        s.add(post)
        s.flush()
        s.add(
            MediaItem(
                creator_id=c.id,
                post_id=post.id,
                media_key="k1",
                kind="video",
                source="embed_youtube",
                status=MediaStatus.COMPLETED,
                file_path=rel,
                sha256=hashlib.sha256(b"same").hexdigest(),
            )
        )
    kit = build(env, factory, settings, bus, providers, direct=FakeHandler(data=b"same"))
    queue_post(factory, kit.lifecycle, fx.native_video_post("p1"), creator_id=patreon_creator)

    outcome = await run_next(kit)

    assert isinstance(outcome, Completed)
    new = env.download_dir / outcome.file_path
    assert new.stat().st_ino == (tmp_path / "yt" / rel).stat().st_ino


def test_same_named_files_downloading_together_get_distinct_names(tmp_path):
    # Patreon keeps uploaders' file names, so one post can hold two "image.png"s and
    # they download in parallel. Neither file exists while both are being written,
    # so the name check alone gave both the same path (and the same .part file).
    reservations = Reservations()
    target = tmp_path / "Creator" / "Post" / "image.png"
    assert reservations.reserve(1, target) == target
    assert reservations.reserve(2, target) == target.with_name("image (2).png")
    reservations.release(1)  # job 1 finished; its name is free again
    assert reservations.reserve(3, target) == target

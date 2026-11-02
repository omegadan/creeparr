"""The download workers end to end: queued media → claimed → run → recorded."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from creeparr.db.engine import session_scope
from creeparr.db.enums import JobStatus, MediaStatus, PostStatus
from creeparr.db.models import DownloadJob, History, MediaItem, Post
from creeparr.downloader.handlers.base import MediaSnapshot, Placement, ProgressReporter
from creeparr.downloader.progress import JobProgress
from creeparr.patreon.client import API_URL
from tests import patreon_fixtures as fx
from tests.conftest import json_response
from tests.downloads import (
    FakeHandler,
    build,
    fake_ytdlp,
    job,
    patreon_registry,
    queue_post,
    wait_for,
)

VIDEO_URL = f"{fx.CDN}/p1/episode.mp4?token=x"


@pytest.fixture
async def providers(env, settings, session_factory, bus):
    registry = patreon_registry(env, settings, session_factory, bus)
    yield registry
    await registry.aclose_all()


def status_of(session_factory, job_id: int) -> str:
    return job(session_factory, job_id).status


@pytest.mark.asyncio
async def test_direct_download_end_to_end(
    env, session_factory, settings, bus, providers, respx_mock
):
    body = b"\x00\x01" * 50_000
    respx_mock.get(VIDEO_URL).mock(
        return_value=httpx.Response(200, content=body, headers={"content-length": str(len(body))})
    )
    kit = build(env, session_factory, settings, bus, providers)
    post_id, job_id = queue_post(
        session_factory, kit.lifecycle, fx.native_video_post("p1", title="Ep 1: Intro")
    )
    bus.bind(asyncio.get_running_loop())
    await kit.manager.start()
    try:
        assert await wait_for(lambda: status_of(session_factory, job_id) == JobStatus.COMPLETED)
    finally:
        await kit.manager.stop()
    with session_scope(session_factory) as s:
        media = s.execute(select(MediaItem)).scalar_one()
        post = s.get(Post, post_id)
        assert media.status == MediaStatus.COMPLETED
        assert media.file_size_bytes == len(body) and media.sha256
        assert post.status == PostStatus.COMPLETED
        expected_dir = Path("Example Creator") / "2026-03-14 - Ep 1_ Intro [p1]"
        assert Path(media.file_path) == expected_dir / "episode.mp4"
        assert post.folder_path == str(expected_dir)
        assert post.sidecars_written
        events = [h.event_type for h in s.execute(select(History)).scalars()]
        assert "download_completed" in events
    post_dir = env.download_dir / expected_dir
    assert (post_dir / "episode.mp4").read_bytes() == body
    assert (
        (post_dir / "post.json").exists()
        and (post_dir / "post.md").exists()
        and (post_dir / "post.html").exists()
    )
    assert "Hello **world**" in (post_dir / "post.md").read_text()
    assert not list(post_dir.glob(".creeparr-tmp-*"))


@pytest.mark.asyncio
async def test_failed_download_gets_backoff_then_permanent(
    env, session_factory, settings, bus, providers, respx_mock
):
    settings.update({"downloads": {"max_attempts": 2, "retry_base_seconds": 5}})
    respx_mock.get(VIDEO_URL).mock(return_value=httpx.Response(500, content=b"boom"))
    respx_mock.get(f"{API_URL}/posts/p1").mock(
        return_value=json_response(fx.post_response(fx.native_video_post("p1")))
    )
    kit = build(env, session_factory, settings, bus, providers)
    _, job_id = queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1"))
    bus.bind(asyncio.get_running_loop())
    await kit.manager.start()
    try:
        assert await wait_for(lambda: status_of(session_factory, job_id) == JobStatus.FAILED)
        with session_scope(session_factory) as s:
            media = s.execute(select(MediaItem)).scalar_one()
            assert media.status == MediaStatus.FAILED and media.attempts == 1
            assert media.next_retry_at is not None and media.next_retry_at > datetime.now(UTC)
            media.next_retry_at = datetime.now(UTC)
        assert kit.lifecycle.retry_due() == 1
        with session_scope(session_factory) as s:
            job2 = s.execute(select(DownloadJob).where(DownloadJob.id != job_id)).scalar_one()
            job2_id = job2.id
            assert job2.attempt == 2
        assert await wait_for(lambda: status_of(session_factory, job2_id) == JobStatus.FAILED)
    finally:
        await kit.manager.stop()
    with session_scope(session_factory) as s:
        media = s.execute(select(MediaItem).options(selectinload(MediaItem.post))).scalar_one()
        assert media.status == MediaStatus.FAILED_PERMANENT
        assert media.post.status == PostStatus.PARTIAL


@pytest.mark.asyncio
async def test_hls_drm_marks_unsupported(
    env, session_factory, settings, bus, providers, respx_mock
):
    respx_mock.get(fx.MUX_URL.split("?")[0]).mock(
        return_value=httpx.Response(
            200, text=fx.MUX_MASTER_DRM, headers={"content-type": "application/vnd.apple.mpegurl"}
        )
    )
    kit = build(env, session_factory, settings, bus, providers)
    _, job_id = queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1", hls=True))
    bus.bind(asyncio.get_running_loop())
    await kit.manager.start()
    try:
        assert await wait_for(lambda: status_of(session_factory, job_id) == JobStatus.FAILED)
    finally:
        await kit.manager.stop()
    with session_scope(session_factory) as s:
        media = s.execute(select(MediaItem).options(selectinload(MediaItem.post))).scalar_one()
        assert media.status == MediaStatus.UNSUPPORTED_DRM
        assert media.next_retry_at is None
        assert media.post.status == PostStatus.UNSUPPORTED
        assert s.get(DownloadJob, job_id).error_class == "drm"


@pytest.mark.asyncio
async def test_disk_full_pauses(env, session_factory, settings, bus, providers):
    settings.update({"downloads": {"min_free_mb": 1}})
    kit = build(env, session_factory, settings, bus, providers, free_space=lambda _root: 0)
    _, job_id = queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1"))
    bus.bind(asyncio.get_running_loop())
    await kit.manager.start()
    try:
        assert await wait_for(lambda: kit.manager.paused_reason == "disk_full", timeout=5)
        await asyncio.sleep(0.2)
        assert status_of(session_factory, job_id) == JobStatus.QUEUED
    finally:
        await kit.manager.stop()


@pytest.mark.asyncio
async def test_worker_survives_media_deleted_mid_job(
    env, session_factory, settings, bus, providers
):
    # Deleting a creator (or a post edit dropping an item) removes the media row while
    # its download runs. Recording that used to raise out of the worker, which then died
    # for good; with concurrency 1 all downloads stopped.
    settings.update({"downloads": {"concurrency": 1}})

    async def delete_then_crash(media: MediaSnapshot, place, progress) -> None:
        if len(handler.calls) == 1:  # only the first download
            with session_scope(session_factory) as s:
                s.delete(s.get(MediaItem, media.media_id))
            raise RuntimeError("simulated crash after the media row was deleted")

    handler = FakeHandler(before=delete_then_crash)
    kit = build(env, session_factory, settings, bus, providers, direct=handler)
    queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1", title="Ep 1"))
    bus.bind(asyncio.get_running_loop())
    await kit.manager.start()
    try:
        assert await wait_for(lambda: bool(handler.calls))
        await asyncio.sleep(0.2)  # its job went with the media row
        # A later item must still be picked up by the same (single) worker.
        _, second_job = queue_post(
            session_factory, kit.lifecycle, fx.native_video_post("p2", title="Ep 2")
        )
        assert await wait_for(lambda: status_of(session_factory, second_job) == JobStatus.COMPLETED)
        assert kit.manager.status()["workers"] == 1
    finally:
        await kit.manager.stop()


@pytest.mark.asyncio
async def test_cancelling_a_running_download_cancels_its_media(
    env, session_factory, settings, bus, providers
):
    async def stream_until_cancelled(media, place: Placement, progress: ProgressReporter):
        while True:  # update() raises DownloadCancelled once the flag is set
            progress.update(downloaded=1, total=10)
            await asyncio.sleep(0.02)

    kit = build(
        env,
        session_factory,
        settings,
        bus,
        providers,
        direct=FakeHandler(before=stream_until_cancelled),
    )
    _, job_id = queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1"))
    bus.bind(asyncio.get_running_loop())
    await kit.manager.start()
    try:
        assert await wait_for(lambda: status_of(session_factory, job_id) == JobStatus.RUNNING)
        assert kit.manager.cancel_job(job_id)
        assert await wait_for(lambda: status_of(session_factory, job_id) == JobStatus.CANCELLED)
    finally:
        await kit.manager.stop()
    with session_scope(session_factory) as s:
        media = s.execute(select(MediaItem)).scalar_one()
        assert (media.status, media.status_reason) == (MediaStatus.CANCELLED, "cancelled by user")


@pytest.mark.asyncio
async def test_a_job_queued_from_another_thread_wakes_an_idle_worker(
    env, session_factory, settings, bus, providers
):
    # Scans and API requests queue downloads from their own threads; the lifecycle then
    # wakes the workers, which would otherwise poll only every 5 seconds.
    kit = build(env, session_factory, settings, bus, providers, direct=FakeHandler())
    bus.bind(asyncio.get_running_loop())
    await kit.manager.start()
    try:
        await asyncio.sleep(0.3)  # the workers found nothing and are idle
        started = time.monotonic()
        _, job_id = await asyncio.to_thread(
            queue_post, session_factory, kit.lifecycle, fx.native_video_post("p1")
        )
        assert await wait_for(
            lambda: status_of(session_factory, job_id) == JobStatus.COMPLETED, timeout=3
        )
        assert time.monotonic() - started < 3
    finally:
        await kit.manager.stop()


@pytest.mark.asyncio
async def test_long_work_runs_in_its_own_pool(env, session_factory, settings, bus, providers):
    # yt-dlp can hold a thread for hours; it must not occupy asyncio's small default
    # pool, which claims, DB updates and scans all share.
    engine = fake_ytdlp()
    kit = build(env, session_factory, settings, bus, providers, engine=engine)
    _, job_id = queue_post(session_factory, kit.lifecycle, fx.youtube_post("y1", title="Clip"))
    bus.bind(asyncio.get_running_loop())
    await kit.manager.start()
    try:
        assert await wait_for(lambda: status_of(session_factory, job_id) == JobStatus.COMPLETED)
    finally:
        await kit.manager.stop()
    assert engine.threads and engine.threads[0].startswith("creeparr-long")


def set_running(session_factory, job_id: int) -> None:
    with session_scope(session_factory) as s:
        s.get(DownloadJob, job_id).status = JobStatus.RUNNING


@pytest.mark.asyncio
async def test_progress_is_written_off_the_event_loop(
    env, engine, session_factory, settings, bus, lifecycle
):
    # Direct downloads report from the event loop. Writing there would stall the whole
    # server while another writer (a scan) holds SQLite's write lock.
    _, job_id = queue_post(session_factory, lifecycle, fx.native_video_post("p1"))
    set_running(session_factory, job_id)
    reporter = JobProgress(session_factory, bus).reporter(job_id)
    writer = sqlite3.connect(engine.url.database, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")  # hold the write lock
    try:
        started = time.monotonic()
        reporter.update(downloaded=40, total=100)
        assert time.monotonic() - started < 0.5
    finally:
        writer.execute("COMMIT")
        writer.close()
    assert await wait_for(lambda: job(session_factory, job_id).progress_percent == 40.0)


@pytest.mark.asyncio
async def test_progress_after_the_download_finished_is_ignored(session_factory, bus, lifecycle):
    _, job_id = queue_post(session_factory, lifecycle, fx.native_video_post("p1"))
    with session_scope(session_factory) as s:
        row = s.get(DownloadJob, job_id)
        row.status, row.progress_percent, row.stage = JobStatus.COMPLETED, 100.0, None
    reporter = JobProgress(session_factory, bus).reporter(job_id)
    # yt-dlp's hooks report from its own thread, where the row is written right away.
    await asyncio.to_thread(reporter.update, downloaded=97, total=100)
    row = job(session_factory, job_id)
    assert (row.progress_percent, row.stage) == (100.0, None)

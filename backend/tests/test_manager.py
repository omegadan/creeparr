"""End-to-end: queued media -> DownloadManager -> file on disk (direct handler, respx-served)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from patrearr.db.engine import session_scope
from patrearr.db.enums import AuthState, JobStatus, MediaStatus, PostStatus
from patrearr.db.models import Creator, DownloadJob, History, MediaItem, Post
from patrearr.downloader.manager import DownloadManager
from patrearr.downloader.queue import enqueue_media
from patrearr.patreon.client import API_URL
from patrearr.patreon.media_resolver import resolve_media
from patrearr.patreon.parsing import IncludedIndex, post_from_resource
from patrearr.providers.patreon import PatreonProvider
from patrearr.providers.registry import ProviderRegistry
from patrearr.scanner.scanner import sync_media_items, upsert_post
from tests import patreon_fixtures as fx
from tests.conftest import json_response

VIDEO_URL = f"{fx.CDN}/p1/episode.mp4?token=x"


def seed(session_factory, resource) -> tuple[int, int]:
    page = fx.posts_page([resource])
    pr = post_from_resource(resource, IncludedIndex(page))
    with session_scope(session_factory) as s:
        creator = Creator(campaign_id=fx.CAMPAIGN_ID, name="Example Creator", vanity=fx.VANITY)
        s.add(creator)
        s.flush()
        post, _, _ = upsert_post(s, creator, pr)
        items = sync_media_items(s, creator, post, resolve_media(pr))
        job = enqueue_media(s, items[0])
        return post.id, job.id


async def wait_for(pred, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


def job_status(session_factory, job_id):
    with session_scope(session_factory) as s:
        return s.get(DownloadJob, job_id).status


@pytest.fixture
def providers(env, settings, session_factory, bus):
    settings.update(
        {"patreon": {"session_id": "sid-123", "requests_per_second": 10}}, allow_secrets=True
    )
    registry = ProviderRegistry([PatreonProvider(env, settings, session_factory, bus)])
    registry.get("patreon")._set_auth_status(AuthState.VALID, user_name="Test Patron")
    return registry


@pytest.mark.asyncio
async def test_direct_download_end_to_end(
    env, session_factory, settings, bus, providers, respx_mock
):
    body = b"\x00\x01" * 50_000
    respx_mock.get(VIDEO_URL).mock(
        return_value=httpx.Response(200, content=body, headers={"content-length": str(len(body))})
    )
    post_id, job_id = seed(session_factory, fx.native_video_post("p1", title="Ep 1: Intro"))
    bus.bind(asyncio.get_running_loop())
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    await mgr.start()
    try:
        assert await wait_for(lambda: job_status(session_factory, job_id) == JobStatus.COMPLETED)
    finally:
        await mgr.stop()
        await providers.aclose_all()
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
    assert not list(post_dir.glob(".patrearr-tmp-*"))


@pytest.mark.asyncio
async def test_failed_download_gets_backoff_then_permanent(
    env, session_factory, settings, bus, providers, respx_mock
):
    settings.update({"downloads": {"max_attempts": 2, "retry_base_seconds": 5}})
    respx_mock.get(VIDEO_URL).mock(return_value=httpx.Response(500, content=b"boom"))
    respx_mock.get(f"{API_URL}/posts/p1").mock(
        return_value=json_response(fx.post_response(fx.native_video_post("p1")))
    )
    _, job_id = seed(session_factory, fx.native_video_post("p1"))
    bus.bind(asyncio.get_running_loop())
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    await mgr.start()
    try:
        assert await wait_for(lambda: job_status(session_factory, job_id) == JobStatus.FAILED)
        with session_scope(session_factory) as s:
            media = s.execute(select(MediaItem)).scalar_one()
            assert media.status == MediaStatus.FAILED and media.attempts == 1
            assert media.next_retry_at is not None and media.next_retry_at > datetime.now(UTC)
            media.next_retry_at = datetime.now(UTC)
        assert mgr.requeue_due_retries() == 1
        with session_scope(session_factory) as s:
            job2 = s.execute(
                select(DownloadJob).where(DownloadJob.status == JobStatus.QUEUED)
            ).scalar_one()
            job2_id = job2.id
            assert job2.attempt == 2
        assert await wait_for(lambda: job_status(session_factory, job2_id) == JobStatus.FAILED)
    finally:
        await mgr.stop()
        await providers.aclose_all()
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
    _, job_id = seed(session_factory, fx.native_video_post("p1", hls=True))
    bus.bind(asyncio.get_running_loop())
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    await mgr.start()
    try:
        assert await wait_for(lambda: job_status(session_factory, job_id) == JobStatus.FAILED)
    finally:
        await mgr.stop()
        await providers.aclose_all()
    with session_scope(session_factory) as s:
        media = s.execute(select(MediaItem).options(selectinload(MediaItem.post))).scalar_one()
        assert media.status == MediaStatus.UNSUPPORTED_DRM
        assert media.next_retry_at is None
        assert media.post.status == PostStatus.UNSUPPORTED
        job = s.get(DownloadJob, job_id)
        assert job.error_class == "drm"


@pytest.mark.asyncio
async def test_disk_full_pauses(env, session_factory, settings, bus, providers, monkeypatch):
    settings.update({"downloads": {"min_free_mb": 1}})
    monkeypatch.setattr("patrearr.downloader.manager.free_space_bytes", lambda _p: 0)
    _, job_id = seed(session_factory, fx.native_video_post("p1"))
    bus.bind(asyncio.get_running_loop())
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    await mgr.start()
    try:
        assert await wait_for(lambda: mgr.paused_reason == "disk_full", timeout=5)
        await asyncio.sleep(0.2)
        assert job_status(session_factory, job_id) == JobStatus.QUEUED
    finally:
        await mgr.stop()
        await providers.aclose_all()


@pytest.mark.asyncio
async def test_recover_stale_jobs(env, session_factory, settings, bus, providers):
    _, job_id = seed(session_factory, fx.native_video_post("p1"))
    with session_scope(session_factory) as s:
        job = s.get(DownloadJob, job_id)
        job.status = JobStatus.RUNNING
        s.get(MediaItem, job.media_item_id).status = MediaStatus.DOWNLOADING
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    mgr._recover_stale_jobs()
    with session_scope(session_factory) as s:
        assert s.get(DownloadJob, job_id).status == JobStatus.QUEUED
        assert s.execute(select(MediaItem)).scalar_one().status == MediaStatus.QUEUED
    await providers.aclose_all()

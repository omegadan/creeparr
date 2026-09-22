"""End-to-end: queued media -> DownloadManager -> file on disk (direct handler, respx-served)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from creeparr.db.engine import session_scope
from creeparr.db.enums import AuthState, JobStatus, MediaStatus, PostStatus
from creeparr.db.models import Creator, DownloadJob, History, MediaItem, Post
from creeparr.downloader.manager import DownloadManager
from creeparr.downloader.queue import enqueue_media
from creeparr.patreon.client import API_URL
from creeparr.patreon.media_resolver import resolve_media
from creeparr.patreon.parsing import IncludedIndex, post_from_resource
from creeparr.providers.patreon import PatreonProvider
from creeparr.providers.registry import ProviderRegistry
from creeparr.scanner.scanner import sync_media_items, upsert_post
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
    monkeypatch.setattr("creeparr.downloader.manager.free_space_bytes", lambda _p: 0)
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


@pytest.mark.asyncio
async def test_hourly_limit_persists_across_restart(env, session_factory, settings, bus, providers):
    # One download already started within the last hour, recorded as a job row.
    _, done_job = seed(session_factory, fx.native_video_post("p1", title="Ep 1"))
    with session_scope(session_factory) as s:
        j = s.get(DownloadJob, done_job)
        j.status = JobStatus.COMPLETED
        j.started_at = datetime.now(UTC)
    # A second item is queued and waiting to download.
    res2 = fx.native_video_post("p2", title="Ep 2")
    page2 = fx.posts_page([res2])
    pr2 = post_from_resource(res2, IncludedIndex(page2))
    with session_scope(session_factory) as s:
        creator = s.execute(select(Creator)).scalar_one()
        post, _, _ = upsert_post(s, creator, pr2)
        items = sync_media_items(s, creator, post, resolve_media(pr2))
        enqueue_media(s, items[0])

    settings.update(
        {"patreon": {"downloads_per_hour": 1}, "downloads": {"spread_downloads": False}}
    )
    # A brand-new manager (as after a restart, with empty in-memory state) must
    # still count the persisted earlier start and refuse to exceed the limit.
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    assert mgr._claim_next("w0") is None
    status = {p["provider"]: p for p in mgr.provider_status()}["patreon"]
    assert status["state"] == "throttled" and status["recent_starts"] == 1
    assert status["next_slot_at"] is not None

    # Raising the limit frees it, proving the block was the persisted count.
    settings.update({"patreon": {"downloads_per_hour": 5}})
    assert mgr._claim_next("w0") is not None


@pytest.mark.asyncio
async def test_provider_status_creators_off(env, session_factory, settings, bus, providers):
    # A queued item whose creator is switched off must not read as "waiting".
    seed(session_factory, fx.native_video_post("p1", title="Ep 1"))
    with session_scope(session_factory) as s:
        s.execute(select(Creator)).scalar_one().enabled = False
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    status = {p["provider"]: p for p in mgr.provider_status()}["patreon"]
    assert status["state"] == "creators_off"
    assert status["queued"] == 1
    # And the worker will not claim it while the creator is disabled.
    assert mgr._claim_next("w0") is None


def add_queued_post(session_factory, resource) -> int:
    page = fx.posts_page([resource])
    pr = post_from_resource(resource, IncludedIndex(page))
    with session_scope(session_factory) as s:
        creator = s.execute(select(Creator)).scalar_one()
        post, _, _ = upsert_post(s, creator, pr)
        items = sync_media_items(s, creator, post, resolve_media(pr))
        return enqueue_media(s, items[0]).id


@pytest.mark.asyncio
async def test_worker_survives_media_deleted_mid_job(
    env, session_factory, settings, bus, providers, respx_mock
):
    # Deleting a creator (or a post edit dropping an item) removes the media row while
    # its job runs. Recording that job's outcome used to raise NoResultFound out of the
    # worker, which then died for good; with concurrency 1 all downloads stopped.
    settings.update({"downloads": {"concurrency": 1}})
    _, first_job = seed(session_factory, fx.native_video_post("p1", title="Ep 1"))
    bus.bind(asyncio.get_running_loop())
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    real_run_job = mgr._run_job
    ran: list[int] = []

    async def run_job(ctx, reporter):
        ran.append(ctx.job_id)
        if len(ran) == 1:  # only the first job (SQLite may reuse its id once it's gone)
            with session_scope(session_factory) as s:
                s.delete(s.get(MediaItem, ctx.media_id))
            raise RuntimeError("simulated crash after the media row was deleted")
        await real_run_job(ctx, reporter)

    mgr._run_job = run_job
    await mgr.start()
    try:
        assert await wait_for(lambda: first_job in ran)
        await asyncio.sleep(0.2)
        # A later item must still be picked up by the same (single) worker.
        body = b"\x00" * 1000
        respx_mock.get(f"{fx.CDN}/p2/episode.mp4?token=x").mock(
            return_value=httpx.Response(200, content=body)
        )
        second_job = add_queued_post(session_factory, fx.native_video_post("p2", title="Ep 2"))
        mgr._kick.set()
        assert await wait_for(
            lambda: job_status(session_factory, second_job) == JobStatus.COMPLETED
        )
        assert mgr.status()["workers"] == 1
    finally:
        await mgr.stop()
        await providers.aclose_all()


def queue_posts(session_factory, creator_id: int, post_ids: list[str]) -> list[int]:
    job_ids = []
    with session_scope(session_factory) as s:
        creator = s.get(Creator, creator_id)
        for pid in post_ids:
            res = fx.native_video_post(pid, title=f"Ep {pid}")
            pr = post_from_resource(res, IncludedIndex(fx.posts_page([res])))
            post, _, _ = upsert_post(s, creator, pr)
            items = sync_media_items(s, creator, post, resolve_media(pr))
            job_ids.append(enqueue_media(s, items[0]).id)
    return job_ids


@pytest.mark.asyncio
async def test_claim_is_not_starved_by_a_long_blocked_backlog(
    env, session_factory, settings, bus, providers
):
    # A slow archive can have tens of thousands of queued jobs. Claiming used to look
    # only at the first 200 in queue order, so a big backlog that couldn't start (a
    # disabled creator here; equally a throttled provider or a creator at its cap)
    # hid everything behind it forever.
    with session_scope(session_factory) as s:
        off = Creator(campaign_id="1", name="Paused Creator", enabled=False)
        on = Creator(campaign_id="2", name="Active Creator")
        s.add_all([off, on])
        s.flush()
        off_id, on_id = off.id, on.id
    queue_posts(session_factory, off_id, [f"a{i}" for i in range(250)])
    (waiting,) = queue_posts(session_factory, on_id, ["b1"])

    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    ctx = mgr._claim_next("w0")
    assert ctx is not None and ctx.job_id == waiting
    await providers.aclose_all()


@pytest.mark.asyncio
async def test_claim_skips_creators_at_their_cap_in_the_query(
    env, session_factory, settings, bus, providers
):
    settings.update({"downloads": {"max_per_creator": 1}})
    with session_scope(session_factory) as s:
        busy = Creator(campaign_id="1", name="Busy")
        idle = Creator(campaign_id="2", name="Idle")
        s.add_all([busy, idle])
        s.flush()
        busy_id, idle_id = busy.id, idle.id
    first, *_ = queue_posts(session_factory, busy_id, [f"a{i}" for i in range(250)])
    (other,) = queue_posts(session_factory, idle_id, ["b1"])

    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    assert mgr._claim_next("w0").job_id == first  # Busy is now at its cap of 1
    assert mgr._claim_next("w1").job_id == other
    assert mgr._claim_next("w2") is None  # everything left belongs to Busy
    await providers.aclose_all()

"""The library's upkeep: files that vanish become missing and come back, the embed backlog."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from creeparr.core.lifecycle import MISSING_REASON, derive_post_status
from creeparr.db.engine import session_scope
from creeparr.db.enums import EventType, JobStatus, MediaStatus, PostStatus
from creeparr.db.models import Creator, DownloadJob, History, MediaItem
from creeparr.downloader.library import UNMOUNTED_MIN_FILES, root_unavailable
from creeparr.patreon.parsing import IncludedIndex, post_from_resource
from tests import patreon_fixtures as fx
from tests.downloads import FakeFfmpeg, Kit, build, patreon_registry

FILE_REL = "Example Creator/2026-03-14 - Ep 1 [p1]/episode.mp4"
NOTHING = {"checked": 1, "missing": 0, "restored": 0, "requeued": 0, "skipped": 0}


@pytest.fixture
async def providers(env, settings, session_factory, bus):
    registry = patreon_registry(env, settings, session_factory, bus)
    yield registry
    await registry.aclose_all()


@pytest.fixture
def kit(env, session_factory, settings, bus, providers) -> Kit:
    return build(env, session_factory, settings, bus, providers, ffmpeg=FakeFfmpeg("ffmpeg"))


def seed_completed(env, session_factory, kit: Kit) -> tuple[int, int]:
    """An archived video whose file is on disk. Returns (post id, media id)."""
    resource = fx.native_video_post("p1", title="Ep 1")
    pr = post_from_resource(resource, IncludedIndex(fx.posts_page([resource])))
    path = env.download_dir / FILE_REL
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")
    with session_scope(session_factory) as s:
        creator = Creator(
            campaign_id=fx.CAMPAIGN_ID,
            name="Example Creator",
            vanity=fx.VANITY,
            auto_download=False,
        )
        s.add(creator)
        s.flush()
        post = kit.lifecycle.sync_post(s, creator, pr, fx.POSTS).post
        item = post.media_items[0]
        item.status = MediaStatus.COMPLETED
        item.file_path = FILE_REL
        item.file_size_bytes = 5
        item.completed_at = datetime.now(UTC)
        post.status = derive_post_status(post)
        s.flush()
        return post.id, item.id


def media_state(session_factory, media_id: int) -> tuple[str, str | None, str]:
    with session_scope(session_factory) as s:
        m = s.get(MediaItem, media_id)
        return m.status, m.status_reason, m.post.status


def test_verify_files_marks_missing_then_restores(env, session_factory, kit):
    post_id, media_id = seed_completed(env, session_factory, kit)

    # file present: nothing changes
    assert kit.library.verify_files() == NOTHING
    assert media_state(session_factory, media_id)[0] == MediaStatus.COMPLETED

    # file gone: item becomes missing, post is no longer fully archived, history written
    (env.download_dir / FILE_REL).unlink()
    assert kit.library.verify_files() == {**NOTHING, "missing": 1}
    status, reason, post_status = media_state(session_factory, media_id)
    assert status == MediaStatus.MISSING
    assert reason == MISSING_REASON
    assert post_status != PostStatus.COMPLETED
    with session_scope(session_factory) as s:
        ev = s.execute(
            select(History).where(History.event_type == EventType.MEDIA_MISSING)
        ).scalar_one()
        assert ev.media_item_id == media_id and ev.post_id == post_id
        assert ev.level == "warning"
        assert s.get(MediaItem, media_id).file_path == FILE_REL  # kept for the record
        assert s.execute(select(DownloadJob)).first() is None  # requeue_missing is off

    # a second run is idempotent
    assert kit.library.verify_files() == NOTHING

    # file comes back (restored from backup / disk re-mounted): completed again
    (env.download_dir / FILE_REL).write_bytes(b"video")
    assert kit.library.verify_files() == {**NOTHING, "restored": 1}
    status, reason, post_status = media_state(session_factory, media_id)
    assert status == MediaStatus.COMPLETED and reason is None
    assert post_status == PostStatus.COMPLETED
    with session_scope(session_factory) as s:
        assert (
            s.execute(select(History).where(History.event_type == EventType.MEDIA_RESTORED))
            .scalars()
            .one()
        )


def test_verify_files_requeues_when_enabled(env, session_factory, settings, kit):
    _, media_id = seed_completed(env, session_factory, kit)
    settings.update({"downloads": {"requeue_missing": True}})
    (env.download_dir / FILE_REL).unlink()

    assert kit.library.verify_files() == {**NOTHING, "missing": 1, "requeued": 1}
    with session_scope(session_factory) as s:
        m = s.get(MediaItem, media_id)
        assert m.status == MediaStatus.QUEUED
        job = s.execute(select(DownloadJob)).scalar_one()
        assert job.media_item_id == media_id and job.status == JobStatus.QUEUED
        assert m.post.status == PostStatus.PENDING


def test_verify_files_task_interval_follows_setting(settings):
    from creeparr.scheduler import SchedulerService

    class Services:  # minimal stand-in for what the scheduler reads
        pass

    services = Services()
    services.settings = settings
    sched = SchedulerService(services)
    sched._register_verify_files()
    assert sched.tasks["verify_files"].interval_seconds is None
    settings.update({"downloads": {"verify_files_hours": 6}})
    sched.apply_settings()
    assert sched.tasks["verify_files"].interval_seconds == 6 * 3600
    settings.update({"downloads": {"verify_files_hours": 0}})
    sched.apply_settings()
    assert sched.tasks["verify_files"].interval_seconds is None


def test_verify_files_leaves_an_unmounted_share_alone(env, session_factory, settings, kit):
    # A share that failed to mount shows up as an empty download root. Treating every
    # file as missing would, with requeue on, re-download the whole archive into it.
    _, media_id = seed_completed(env, session_factory, kit)
    settings.update({"downloads": {"requeue_missing": True}})
    shutil.rmtree(env.download_dir)
    env.download_dir.mkdir()  # the empty mount point

    result = kit.library.verify_files()
    assert result["skipped"] == 1 and result["missing"] == 0 and result["requeued"] == 0
    assert media_state(session_factory, media_id)[0] == MediaStatus.COMPLETED
    with session_scope(session_factory) as s:
        assert s.execute(select(DownloadJob)).first() is None


def test_root_with_many_files_all_gone_counts_as_unmounted(tmp_path):
    (tmp_path / "lost+found").mkdir()  # not empty, but none of the archive is there
    assert root_unavailable(tmp_path, UNMOUNTED_MIN_FILES, 0)
    assert not root_unavailable(tmp_path, UNMOUNTED_MIN_FILES, 1)
    assert not root_unavailable(tmp_path, UNMOUNTED_MIN_FILES - 1, 0)  # small: real deletes
    assert root_unavailable(tmp_path / "nope", 1, 0)


@pytest.mark.asyncio
async def test_the_embed_backlog_embeds_archived_videos_that_lack_it(
    env, session_factory, kit, respx_mock
):
    cover = f"{fx.CDN}/p1/thumb_large.jpg"
    respx_mock.get(cover).mock(return_value=httpx.Response(200, content=b"jpg"))
    _, media_id = seed_completed(env, session_factory, kit)

    assert await kit.library.embed_backlog() == {"embedded": 1, "candidates": 1}

    path = env.download_dir / FILE_REL
    data = path.read_bytes()
    assert data == b"video+tags"
    with session_scope(session_factory) as s:
        m = s.get(MediaItem, media_id)
        assert m.metadata_embedded
        assert (m.file_size_bytes, m.sha256) == (len(data), hashlib.sha256(data).hexdigest())
        published = m.post.published_at
    assert path.stat().st_mtime == published.timestamp()
    (_name, tags, cover_bytes) = kit.ffmpeg.embedded[0]
    assert tags["title"] == "Ep 1" and tags["artist"] == "Example Creator"
    assert cover_bytes == b"jpg"
    assert await kit.library.embed_backlog() == {"embedded": 0, "candidates": 0}


@pytest.mark.asyncio
async def test_the_embed_backlog_waits_for_ffmpeg(env, session_factory, settings, bus, providers):
    kit = build(env, session_factory, settings, bus, providers)  # no ffmpeg installed
    seed_completed(env, session_factory, kit)

    assert await kit.library.embed_backlog() == {"skipped": True, "reason": "ffmpeg not found"}

"""verify_files: completed items whose file vanished become missing, and back again."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from creeparr.db.engine import session_scope
from creeparr.db.enums import AuthState, EventType, JobStatus, MediaStatus, PostStatus
from creeparr.db.models import Creator, DownloadJob, History, MediaItem
from creeparr.downloader.manager import DownloadManager
from creeparr.patreon.media_resolver import resolve_media
from creeparr.patreon.parsing import IncludedIndex, post_from_resource
from creeparr.providers.patreon import PatreonProvider
from creeparr.providers.registry import ProviderRegistry
from creeparr.scanner.scanner import sync_media_items, upsert_post
from tests import patreon_fixtures as fx

FILE_REL = "Example Creator/2026-03-14 - Ep 1 [p1]/episode.mp4"


def seed_completed(env, session_factory) -> tuple[int, int]:
    resource = fx.native_video_post("p1", title="Ep 1")
    pr = post_from_resource(resource, IncludedIndex(fx.posts_page([resource])))
    path = env.download_dir / FILE_REL
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")
    with session_scope(session_factory) as s:
        creator = Creator(campaign_id=fx.CAMPAIGN_ID, name="Example Creator", vanity=fx.VANITY)
        s.add(creator)
        s.flush()
        post, _, _ = upsert_post(s, creator, pr)
        (item,) = sync_media_items(s, creator, post, resolve_media(pr))[:1]
        item.status = MediaStatus.COMPLETED
        item.file_path = FILE_REL
        item.file_size_bytes = 5
        item.completed_at = datetime.now(UTC)
        post.status = PostStatus.COMPLETED
        s.flush()
        return post.id, item.id


def make_manager(env, settings, session_factory, bus) -> DownloadManager:
    settings.update(
        {"patreon": {"session_id": "sid-123", "requests_per_second": 10}}, allow_secrets=True
    )
    registry = ProviderRegistry([PatreonProvider(env, settings, session_factory, bus)])
    registry.get("patreon")._set_auth_status(AuthState.VALID, user_name="Test Patron")
    return DownloadManager(env, session_factory, settings, bus, registry)


def media_state(session_factory, media_id: int) -> tuple[str, str | None, str]:
    with session_scope(session_factory) as s:
        m = s.get(MediaItem, media_id)
        return m.status, m.status_reason, m.post.status


def test_verify_files_marks_missing_then_restores(env, session_factory, settings, bus):
    post_id, media_id = seed_completed(env, session_factory)
    mgr = make_manager(env, settings, session_factory, bus)

    # file present: nothing changes
    assert mgr.verify_files() == {
        "checked": 1,
        "missing": 0,
        "restored": 0,
        "requeued": 0,
        "skipped": 0,
    }
    assert media_state(session_factory, media_id)[0] == MediaStatus.COMPLETED

    # file gone: item becomes missing, post is no longer fully archived, history written
    (env.download_dir / FILE_REL).unlink()
    assert mgr.verify_files() == {
        "checked": 1,
        "missing": 1,
        "restored": 0,
        "requeued": 0,
        "skipped": 0,
    }
    status, reason, post_status = media_state(session_factory, media_id)
    assert status == MediaStatus.MISSING
    assert reason == DownloadManager.MISSING_REASON
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
    assert mgr.verify_files() == {
        "checked": 1,
        "missing": 0,
        "restored": 0,
        "requeued": 0,
        "skipped": 0,
    }

    # file comes back (restored from backup / disk re-mounted): completed again
    (env.download_dir / FILE_REL).write_bytes(b"video")
    assert mgr.verify_files() == {
        "checked": 1,
        "missing": 0,
        "restored": 1,
        "requeued": 0,
        "skipped": 0,
    }
    status, reason, post_status = media_state(session_factory, media_id)
    assert status == MediaStatus.COMPLETED and reason is None
    assert post_status == PostStatus.COMPLETED
    with session_scope(session_factory) as s:
        assert (
            s.execute(select(History).where(History.event_type == EventType.MEDIA_RESTORED))
            .scalars()
            .one()
        )


def test_verify_files_requeues_when_enabled(env, session_factory, settings, bus):
    _, media_id = seed_completed(env, session_factory)
    mgr = make_manager(env, settings, session_factory, bus)
    settings.update({"downloads": {"requeue_missing": True}})
    (env.download_dir / FILE_REL).unlink()

    assert mgr.verify_files() == {
        "checked": 1,
        "missing": 1,
        "restored": 0,
        "requeued": 1,
        "skipped": 0,
    }
    with session_scope(session_factory) as s:
        m = s.get(MediaItem, media_id)
        assert m.status == MediaStatus.QUEUED
        job = s.execute(select(DownloadJob)).scalar_one()
        assert job.media_item_id == media_id and job.status == JobStatus.QUEUED
        assert m.post.status == PostStatus.PENDING


def test_verify_files_task_interval_follows_setting(env, session_factory, settings, bus):
    from creeparr.scheduler import SchedulerService

    class Services:  # minimal stand-in for what the scheduler reads
        pass

    services = Services()
    services.settings = settings
    services.downloads = make_manager(env, settings, session_factory, bus)
    sched = SchedulerService(services)
    sched._register_verify_files()
    assert sched.tasks["verify_files"].interval_seconds is None
    settings.update({"downloads": {"verify_files_hours": 6}})
    sched.apply_settings()
    assert sched.tasks["verify_files"].interval_seconds == 6 * 3600
    settings.update({"downloads": {"verify_files_hours": 0}})
    sched.apply_settings()
    assert sched.tasks["verify_files"].interval_seconds is None


def test_verify_files_leaves_an_unmounted_share_alone(env, session_factory, settings, bus):
    # A share that failed to mount shows up as an empty download root. Treating every
    # file as missing would, with requeue on, re-download the whole archive into it.
    import shutil

    _, media_id = seed_completed(env, session_factory)
    mgr = make_manager(env, settings, session_factory, bus)
    settings.update({"downloads": {"requeue_missing": True}})
    shutil.rmtree(env.download_dir)
    env.download_dir.mkdir()  # the empty mount point

    result = mgr.verify_files()
    assert result["skipped"] == 1 and result["missing"] == 0 and result["requeued"] == 0
    assert media_state(session_factory, media_id)[0] == MediaStatus.COMPLETED
    with session_scope(session_factory) as s:
        assert s.execute(select(DownloadJob)).first() is None


def test_root_with_many_files_all_gone_counts_as_unmounted(tmp_path):
    from creeparr.downloader.maintenance import UNMOUNTED_MIN_FILES, _root_unavailable

    (tmp_path / "lost+found").mkdir()  # not empty, but none of the archive is there
    assert _root_unavailable(tmp_path, UNMOUNTED_MIN_FILES, 0)
    assert not _root_unavailable(tmp_path, UNMOUNTED_MIN_FILES, 1)
    assert not _root_unavailable(tmp_path, UNMOUNTED_MIN_FILES - 1, 0)  # small: real deletes
    assert _root_unavailable(tmp_path / "nope", 1, 0)

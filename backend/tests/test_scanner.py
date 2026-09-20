from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from patrearr.core.patreon_service import PatreonService
from patrearr.db.engine import session_scope
from patrearr.db.enums import AuthState, JobStatus, MediaStatus, PostStatus, ScanMode, ScanStatus
from patrearr.db.models import Creator, DownloadJob, MediaItem, Post, ScanRun
from patrearr.patreon.errors import AuthError
from patrearr.patreon.models import PostPage
from patrearr.patreon.parsing import IncludedIndex, post_from_resource
from patrearr.scanner.scanner import Scanner
from tests import patreon_fixtures as fx


class FakeClient:
    def __init__(self, pages: list[list[dict]], raise_auth: bool = False):
        self.pages = pages
        self.raise_auth = raise_auth
        self.calls = 0

    async def iter_posts(self, campaign_id: str):
        if self.raise_auth:
            raise AuthError("expired")
        for i, resources in enumerate(self.pages):
            self.calls += 1
            page = fx.posts_page(
                resources, included=[fx.media_resource(f"m{n}") for n in range(1, 6)]
            )
            index = IncludedIndex(page)
            yield PostPage(
                posts=[post_from_resource(r, index) for r in resources],
                next_url="next" if i < len(self.pages) - 1 else None,
            )


class FakePatreon:
    def __init__(self, client):
        self.client = client
        self.invalid = None

    def mark_auth_invalid(self, exc):
        self.invalid = exc

    async def check_session(self):
        return True

    def get_auth_status(self):
        return {"state": AuthState.VALID}


def make_creator(session_factory, **kw) -> int:
    with session_scope(session_factory) as s:
        c = Creator(campaign_id=fx.CAMPAIGN_ID, name="Example Creator", vanity=fx.VANITY, **kw)
        s.add(c)
        s.flush()
        return c.id


def counts(session_factory):
    with session_scope(session_factory) as s:
        posts = s.execute(select(Post)).scalars().all()
        media = s.execute(select(MediaItem)).scalars().all()
        jobs = s.execute(select(DownloadJob)).scalars().all()
        return posts, media, jobs


@pytest.mark.asyncio
async def test_full_scan_creates_posts_media_and_jobs(session_factory, settings, bus):
    cid = make_creator(session_factory)
    fake = FakeClient(
        [[fx.native_video_post("p1"), fx.image_post("p2", ["m1", "m2"])], [fx.youtube_post("p3")]]
    )
    scanner = Scanner(session_factory, settings, bus, FakePatreon(fake))
    run_id = await scanner.scan_creator(cid, ScanMode.AUTO)
    posts, media, jobs = counts(session_factory)
    assert fake.calls == 2
    assert {p.post_id for p in posts} == {"p1", "p2", "p3"}
    by_key = {m.media_key: m for m in media}
    assert by_key["postfile:p1"].status == MediaStatus.QUEUED
    assert by_key["embed:p3"].status == MediaStatus.QUEUED
    # images are not wanted by default -> skipped, no jobs
    assert by_key["media:m1"].status == MediaStatus.SKIPPED and not by_key["media:m1"].wanted
    assert len(jobs) == 2 and all(j.status == JobStatus.QUEUED for j in jobs)
    with session_scope(session_factory) as s:
        run = s.get(ScanRun, run_id)
        assert run.status == ScanStatus.OK and run.mode == ScanMode.FULL
        assert (run.posts_seen, run.posts_new, run.media_queued) == (3, 3, 2)
        creator = s.get(Creator, cid)
        assert creator.last_full_scan_at is not None
        statuses = {p.post_id: p.status for p in s.execute(select(Post)).scalars()}
        assert statuses == {
            "p1": PostStatus.PENDING,
            "p2": PostStatus.NO_MEDIA,
            "p3": PostStatus.PENDING,
        }


@pytest.mark.asyncio
async def test_incremental_scan_stops_after_overlap(session_factory, settings, bus):
    cid = make_creator(session_factory)
    settings.update({"scan": {"overlap_posts": 2}})
    known = [fx.native_video_post(f"k{i}") for i in range(4)]
    scanner = Scanner(
        session_factory, settings, bus, FakePatreon(FakeClient([known[:2], known[2:]]))
    )
    await scanner.scan_creator(cid, ScanMode.FULL)
    # Second run: new post on page 1, then all-known pages; must stop before page 3.
    fake = FakeClient([[fx.native_video_post("new1"), known[0]], [known[1], known[2]], [known[3]]])
    scanner = Scanner(session_factory, settings, bus, FakePatreon(fake))
    await scanner.scan_creator(cid, ScanMode.AUTO)
    assert fake.calls == 2
    posts, _, jobs = counts(session_factory)
    assert len(posts) == 5 and len(jobs) == 5


@pytest.mark.asyncio
async def test_access_flip_and_edit_resync(session_factory, settings, bus):
    cid = make_creator(session_factory)
    locked = fx.native_video_post("p1", can_view=False)
    scanner = Scanner(session_factory, settings, bus, FakePatreon(FakeClient([[locked]])))
    await scanner.scan_creator(cid, ScanMode.FULL)
    with session_scope(session_factory) as s:
        post = s.execute(select(Post).options(selectinload(Post.media_items))).scalar_one()
        assert post.status == PostStatus.NO_ACCESS and post.media_items == []
    unlocked = fx.native_video_post("p1", edited_at="2026-04-01T00:00:00.000+00:00")
    scanner = Scanner(session_factory, settings, bus, FakePatreon(FakeClient([[unlocked]])))
    await scanner.scan_creator(cid, ScanMode.INCREMENTAL)
    with session_scope(session_factory) as s:
        post = s.execute(select(Post).options(selectinload(Post.media_items))).scalar_one()
        assert post.status == PostStatus.PENDING
        assert post.media_items[0].status == MediaStatus.QUEUED


@pytest.mark.asyncio
async def test_download_since_and_auto_download_off(session_factory, settings, bus):
    cid = make_creator(session_factory, download_since=datetime(2026, 6, 1, tzinfo=UTC))
    posts = [
        fx.native_video_post("old", published_at="2026-01-01T00:00:00+00:00"),
        fx.native_video_post("new", published_at="2026-07-01T00:00:00+00:00"),
    ]
    scanner = Scanner(session_factory, settings, bus, FakePatreon(FakeClient([posts])))
    await scanner.scan_creator(cid, ScanMode.FULL)
    _, media, jobs = counts(session_factory)
    st = {m.media_key: m.status for m in media}
    assert st == {"postfile:old": MediaStatus.DISCOVERED, "postfile:new": MediaStatus.QUEUED}
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_auth_error_marks_invalid_and_raises(session_factory, settings, bus):
    cid = make_creator(session_factory)
    patreon = FakePatreon(FakeClient([], raise_auth=True))
    scanner = Scanner(session_factory, settings, bus, patreon)
    with pytest.raises(AuthError):
        await scanner.scan_creator(cid, ScanMode.FULL)
    assert patreon.invalid is not None
    with session_scope(session_factory) as s:
        run = s.execute(select(ScanRun)).scalar_one()
        assert run.status == ScanStatus.ERROR
        assert s.get(Creator, cid).last_scan_status == ScanStatus.ERROR


def test_patreon_service_is_real_type():
    assert PatreonService is not None


@pytest.mark.asyncio
async def test_stale_media_rows_are_removed_and_reresolve_works(session_factory, settings, bus):
    from patrearr.scanner.scanner import reresolve_all

    cid = make_creator(session_factory)
    bad_embed = fx.post_resource(
        "p1",
        post_type="link",
        embed={"provider": "Patreon", "url": "https://www.patreon.com/collection/1"},
    )
    scanner = Scanner(session_factory, settings, bus, FakePatreon(FakeClient([[bad_embed]])))
    await scanner.scan_creator(cid, ScanMode.FULL)
    # Simulate a row produced by an older resolver version.
    with session_scope(session_factory) as s:
        post = s.execute(select(Post)).scalar_one()
        s.add(
            MediaItem(
                post_id=post.id,
                creator_id=cid,
                media_key="embed:p1",
                kind="video",
                source="embed_other",
                status=MediaStatus.FAILED_PERMANENT,
            )
        )
    result = reresolve_all(session_factory, bus)
    assert result["posts"] == 1
    with session_scope(session_factory) as s:
        assert s.execute(select(MediaItem)).scalars().all() == []
        assert s.execute(select(Post)).scalar_one().status == PostStatus.NO_MEDIA

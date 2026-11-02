"""What a provider this version doesn't have (Instagram and Reddit, switched off) leaves behind:
creators, posts and queued downloads. None of it may break the rest, and none of it is changed,
so it would carry on if the provider were registered again."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from creeparr.core.errors import Conflict
from creeparr.db.engine import session_scope
from creeparr.db.enums import JobStatus
from creeparr.db.models import Creator, DownloadJob
from tests import patreon_fixtures as fx
from tests.downloads import FakeFfmpeg, Kit, add_creator, build, patreon_registry, queue_post
from tests.test_library import seed_completed

GONE = "instagram"  # not in the test registry, which only has Patreon


@pytest.fixture
async def providers(env, settings, session_factory, bus):
    registry = patreon_registry(env, settings, session_factory, bus)
    yield registry
    await registry.aclose_all()


@pytest.fixture
def kit(env, session_factory, settings, bus, providers) -> Kit:
    return build(env, session_factory, settings, bus, providers, ffmpeg=FakeFfmpeg("ffmpeg"))


def leftover_creator(session_factory) -> int:
    return add_creator(session_factory, campaign_id="ig-1", name="Leftover", provider=GONE)


def test_its_queued_downloads_are_never_claimed_and_stay_queued(kit, session_factory):
    _, leftover_job = queue_post(
        session_factory,
        kit.lifecycle,
        fx.native_video_post("p1"),
        leftover_creator(session_factory),
    )
    _, patreon_job = queue_post(
        session_factory, kit.lifecycle, fx.native_video_post("p2"), add_creator(session_factory)
    )
    claim = kit.claims.next("w0")
    assert claim is not None and claim.job_id == patreon_job
    assert kit.claims.next("w0") is None
    with session_scope(session_factory) as s:
        job = s.get(DownloadJob, leftover_job)
        assert job.status == JobStatus.QUEUED and job.media_item.attempts == 0


def test_the_queue_page_shows_its_jobs_as_unavailable(kit, session_factory):
    queue_post(
        session_factory,
        kit.lifecycle,
        fx.native_video_post("p1"),
        leftover_creator(session_factory),
    )
    status = {p["provider"]: p for p in kit.claims.provider_status(paused=False)}
    assert status[GONE]["state"] == "unavailable" and status[GONE]["queued"] == 1
    assert status[GONE]["label"] == "Instagram"
    assert status["patreon"]["state"] == "idle"


def test_re_resolving_media_skips_its_posts_and_carries_on(kit, session_factory, providers):
    leftover = leftover_creator(session_factory)
    queue_post(session_factory, kit.lifecycle, fx.native_video_post("p1"), leftover)  # first
    queue_post(
        session_factory, kit.lifecycle, fx.native_video_post("p2"), add_creator(session_factory)
    )
    result = kit.lifecycle.reresolve_all(providers)
    assert result["posts"] == 1 and result["unavailable"] == 1


@pytest.mark.asyncio
async def test_its_archived_videos_get_metadata_without_a_cover(env, session_factory, kit):
    # The cover would be fetched with the provider's session; there is none. (respx isn't
    # mocking the cover URL, so a fetch would fail the test.)
    _, media_id = seed_completed(env, session_factory, kit)
    with session_scope(session_factory) as s:
        s.execute(select(Creator)).scalar_one().provider = GONE
    assert await kit.library.embed_backlog() == {"embedded": 1, "candidates": 1}
    (_name, tags, cover) = kit.ffmpeg.embedded[0]
    assert tags["title"] == "Ep 1" and cover is None


def test_looking_up_its_provider_is_a_conflict_not_a_missing_creator(providers):
    with pytest.raises(Conflict) as exc:
        providers.serving(GONE)
    assert exc.value.code == "provider_unavailable"
    assert "'instagram' isn't available" in exc.value.message

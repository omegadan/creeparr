"""downloads_per_hour + spread_downloads: starts are spaced out at random, not burst."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from creeparr.db.engine import session_scope
from creeparr.db.enums import AuthState, JobStatus
from creeparr.db.models import Creator, DownloadJob
from creeparr.downloader.manager import DownloadManager
from creeparr.downloader.queue import enqueue_media
from creeparr.patreon.media_resolver import resolve_media
from creeparr.patreon.parsing import IncludedIndex, post_from_resource
from creeparr.providers.patreon import PatreonProvider
from creeparr.providers.registry import ProviderRegistry
from creeparr.scanner.scanner import sync_media_items, upsert_post
from tests import patreon_fixtures as fx


@pytest.fixture
def providers(env, settings, session_factory, bus):
    settings.update(
        {"patreon": {"session_id": "sid-123", "requests_per_second": 10}}, allow_secrets=True
    )
    registry = ProviderRegistry([PatreonProvider(env, settings, session_factory, bus)])
    registry.get("patreon")._set_auth_status(AuthState.VALID, user_name="Test Patron")
    return registry


def queue_posts(session_factory, n: int) -> None:
    with session_scope(session_factory) as s:
        creator = s.execute(select(Creator)).scalar_one_or_none()
        if creator is None:
            creator = Creator(campaign_id=fx.CAMPAIGN_ID, name="Example Creator", vanity=fx.VANITY)
            s.add(creator)
            s.flush()
        for i in range(n):
            res = fx.native_video_post(f"p{i}", title=f"Ep {i}")
            pr = post_from_resource(res, IncludedIndex(fx.posts_page([res])))
            post, _, _ = upsert_post(s, creator, pr)
            items = sync_media_items(s, creator, post, resolve_media(pr))
            enqueue_media(s, items[0])


def finish_running(session_factory) -> None:
    """No worker consumes claimed jobs in these tests; complete them by hand
    (keeping started_at, which is what the rate limit and pacing read)."""
    with session_scope(session_factory) as s:
        for job in s.execute(
            select(DownloadJob).where(DownloadJob.status == JobStatus.RUNNING)
        ).scalars():
            job.status = JobStatus.COMPLETED


def patreon_status(mgr: DownloadManager) -> dict:
    finish_running(mgr._factory)
    return {p["provider"]: p for p in mgr.provider_status()}["patreon"]


def test_pacing_gap_is_random_around_even_spacing():
    gaps = {DownloadManager._pacing_gap(4).total_seconds() for _ in range(200)}
    assert all(450 <= g <= 1350 for g in gaps)  # 3600/4 = 900s, jittered 0.5x–1.5x
    assert len(gaps) > 100  # actually random, not a constant


def test_spread_spaces_starts_and_reports_pacing(env, session_factory, settings, bus, providers):
    queue_posts(session_factory, 3)
    settings.update({"patreon": {"downloads_per_hour": 4}, "downloads": {"max_per_creator": 8}})
    mgr = DownloadManager(env, session_factory, settings, bus, providers)

    assert mgr._claim_next("w0") is not None  # first start is immediate
    next_start = mgr._next_start["patreon"]
    gap = (next_start - datetime.now(UTC)).total_seconds()
    assert 440 <= gap <= 1350

    # under the cap (1 of 4) but inside the gap: nothing more starts yet
    assert mgr._claim_next("w1") is None
    st = patreon_status(mgr)
    assert st["state"] == "pacing" and st["recent_starts"] == 1
    assert st["next_slot_at"] == next_start.isoformat()
    assert 0 < st["next_slot_seconds"] <= 1350

    # once the gap has elapsed the next one starts, and a new gap is rolled
    mgr._next_start["patreon"] = datetime.now(UTC) - timedelta(seconds=1)
    assert mgr._claim_next("w1") is not None
    assert mgr._next_start["patreon"] > datetime.now(UTC)
    assert mgr._claim_next("w2") is None


def test_spread_off_bursts_up_to_the_cap(env, session_factory, settings, bus, providers):
    queue_posts(session_factory, 3)
    settings.update(
        {
            "patreon": {"downloads_per_hour": 2},
            "downloads": {"spread_downloads": False, "max_per_creator": 8},
        }
    )
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    assert mgr._claim_next("w0") is not None
    assert mgr._claim_next("w1") is not None
    assert "patreon" not in mgr._next_start
    assert mgr._claim_next("w2") is None  # the hard cap still holds
    assert patreon_status(mgr)["state"] == "throttled"


def test_gap_survives_restart(env, session_factory, settings, bus, providers):
    queue_posts(session_factory, 2)
    settings.update({"patreon": {"downloads_per_hour": 4}, "downloads": {"max_per_creator": 8}})
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    assert mgr._claim_next("w0") is not None

    # A fresh manager has no in-memory gap; it must re-derive one from the
    # persisted start so a restart cannot be used to skip the spacing.
    mgr2 = DownloadManager(env, session_factory, settings, bus, providers)
    finish_running(session_factory)
    assert mgr2._claim_next("w0") is None
    st = patreon_status(mgr2)
    assert st["state"] == "pacing" and st["next_slot_at"] is not None


def test_no_cap_means_no_pacing(env, session_factory, settings, bus, providers):
    queue_posts(session_factory, 2)
    settings.update({"downloads": {"max_per_creator": 8}})
    mgr = DownloadManager(env, session_factory, settings, bus, providers)
    assert mgr._claim_next("w0") is not None
    assert mgr._claim_next("w1") is not None
    assert "patreon" not in mgr._next_start

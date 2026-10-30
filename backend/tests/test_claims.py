"""Claiming, through its interface: which queued download starts next.

Hourly limits and pacing (downloads_per_hour + spread_downloads: starts are spaced out at
random, not burst), the per-creator cap, and creators that are switched off.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from creeparr.db.engine import session_scope
from creeparr.db.enums import JobStatus
from creeparr.db.models import Creator, DownloadJob
from creeparr.downloader.claims import Claims, pacing_gap
from tests import patreon_fixtures as fx
from tests.downloads import Kit, add_creator, build, patreon_registry, queue_post


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
async def providers(env, settings, session_factory, bus):
    registry = patreon_registry(env, settings, session_factory, bus)
    yield registry
    await registry.aclose_all()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def kit(env, session_factory, settings, bus, providers, clock) -> Kit:
    return build(env, session_factory, settings, bus, providers, clock=clock)


def queue_posts(kit: Kit, session_factory, n: int, creator_id: int | None = None) -> list[int]:
    return [
        queue_post(
            session_factory,
            kit.lifecycle,
            fx.native_video_post(f"p{creator_id}-{i}", title=f"Ep {i}"),
            creator_id,
        )[1]
        for i in range(n)
    ]


def finish_running(session_factory) -> None:
    """No worker runs the claimed downloads in these tests; complete them by hand (keeping
    started_at, which is what the hourly limit and pacing read)."""
    with session_scope(session_factory) as s:
        for job in s.execute(
            select(DownloadJob).where(DownloadJob.status == JobStatus.RUNNING)
        ).scalars():
            job.status = JobStatus.COMPLETED


def patreon_status(claims: Claims, session_factory) -> dict:
    finish_running(session_factory)
    return {p["provider"]: p for p in claims.provider_status(paused=False)}["patreon"]


def test_pacing_gap_is_random_around_even_spacing():
    gaps = {pacing_gap(4).total_seconds() for _ in range(200)}
    assert all(450 <= g <= 1350 for g in gaps)  # 3600/4 = 900s, jittered 0.5x–1.5x
    assert len(gaps) > 100  # actually random, not a constant


def test_spread_spaces_starts_and_reports_pacing(kit, session_factory, settings, clock):
    queue_posts(kit, session_factory, 3)
    settings.update({"patreon": {"downloads_per_hour": 4}, "downloads": {"max_per_creator": 8}})

    assert kit.claims.next("w0") is not None  # the first start is immediate
    # Under the cap (1 of 4), but inside the gap: nothing more starts yet.
    assert kit.claims.next("w1") is None
    st = patreon_status(kit.claims, session_factory)
    assert st["state"] == "pacing" and st["recent_starts"] == 1
    assert 449 <= st["next_slot_seconds"] <= 1350
    assert datetime.fromisoformat(st["next_slot_at"]) > clock.now

    # Once the gap has passed the next one starts, and a new gap is rolled.
    clock.advance(st["next_slot_seconds"] + 1)
    assert kit.claims.next("w1") is not None
    assert kit.claims.next("w2") is None
    assert patreon_status(kit.claims, session_factory)["state"] == "pacing"


def test_spread_off_bursts_up_to_the_cap(kit, session_factory, settings):
    queue_posts(kit, session_factory, 3)
    settings.update(
        {
            "patreon": {"downloads_per_hour": 2},
            "downloads": {"spread_downloads": False, "max_per_creator": 8},
        }
    )
    assert kit.claims.next("w0") is not None
    assert kit.claims.next("w1") is not None  # no gap between them
    assert kit.claims.next("w2") is None  # the hourly limit still holds
    assert patreon_status(kit.claims, session_factory)["state"] == "throttled"


def test_gap_survives_restart(kit, session_factory, settings, providers, clock):
    queue_posts(kit, session_factory, 2)
    settings.update({"patreon": {"downloads_per_hour": 4}, "downloads": {"max_per_creator": 8}})
    assert kit.claims.next("w0") is not None

    # A fresh Claims has no gap in memory; it must re-derive one from the persisted
    # start, so a restart cannot be used to skip the spacing.
    restarted = Claims(session_factory, settings, providers, kit.lifecycle, clock=clock)
    finish_running(session_factory)
    assert restarted.next("w0") is None
    st = patreon_status(restarted, session_factory)
    assert st["state"] == "pacing" and st["next_slot_at"] is not None


def test_no_cap_means_no_pacing(kit, session_factory, settings):
    queue_posts(kit, session_factory, 2)
    settings.update({"downloads": {"max_per_creator": 8}})
    assert kit.claims.next("w0") is not None
    assert kit.claims.next("w1") is not None


def test_hourly_limit_persists_across_restart(kit, session_factory, settings, providers, clock):
    # One download already started within the last hour, recorded as a job row.
    done, _waiting = queue_posts(kit, session_factory, 2)
    with session_scope(session_factory) as s:
        j = s.get(DownloadJob, done)
        j.status = JobStatus.COMPLETED
        j.started_at = datetime.now(UTC)
    settings.update(
        {"patreon": {"downloads_per_hour": 1}, "downloads": {"spread_downloads": False}}
    )
    # A brand-new Claims (as after a restart, with nothing in memory) must still count
    # the persisted earlier start and refuse to exceed the limit.
    claims = Claims(session_factory, settings, providers, kit.lifecycle, clock=clock)
    assert claims.next("w0") is None
    status = patreon_status(claims, session_factory)
    assert status["state"] == "throttled" and status["recent_starts"] == 1
    assert status["next_slot_at"] is not None

    # Raising the limit frees it, proving the block was the persisted count.
    settings.update({"patreon": {"downloads_per_hour": 5}})
    assert claims.next("w0") is not None


def test_queued_downloads_of_switched_off_creators_never_start(kit, session_factory):
    queue_posts(kit, session_factory, 1)
    with session_scope(session_factory) as s:
        s.execute(select(Creator)).scalar_one().enabled = False
    # It must not read as "waiting" either.
    status = patreon_status(kit.claims, session_factory)
    assert status["state"] == "creators_off" and status["queued"] == 1
    assert kit.claims.next("w0") is None


def test_claim_is_not_starved_by_a_long_blocked_backlog(kit, session_factory):
    # A slow archive can have tens of thousands of queued jobs. Claiming used to look
    # only at the first 200 in queue order, so a big backlog that couldn't start (a
    # disabled creator here; equally a throttled provider or a creator at its cap)
    # hid everything behind it forever.
    off = add_creator(session_factory, campaign_id="1", name="Paused Creator", enabled=False)
    on = add_creator(session_factory, campaign_id="2", name="Active Creator")
    queue_posts(kit, session_factory, 250, off)
    (waiting,) = queue_posts(kit, session_factory, 1, on)

    claim = kit.claims.next("w0")
    assert claim is not None and claim.job_id == waiting


def test_claim_skips_creators_at_their_cap_in_the_query(kit, session_factory, settings):
    settings.update({"downloads": {"max_per_creator": 1}})
    busy = add_creator(session_factory, campaign_id="1", name="Busy")
    idle = add_creator(session_factory, campaign_id="2", name="Idle")
    first, *_ = queue_posts(kit, session_factory, 250, busy)
    (other,) = queue_posts(kit, session_factory, 1, idle)

    assert kit.claims.next("w0").job_id == first  # Busy is now at its cap of 1
    assert kit.claims.next("w1").job_id == other
    assert kit.claims.next("w2") is None  # everything left belongs to Busy


def test_restart_keeps_interrupted_starts_in_the_hourly_window(kit, session_factory):
    (job_id,) = queue_posts(kit, session_factory, 1)
    assert kit.claims.next("w0").job_id == job_id

    assert kit.lifecycle.recover_interrupted() == 1  # as on the next start

    with session_scope(session_factory) as s:
        job = s.get(DownloadJob, job_id)
        assert job.status == JobStatus.QUEUED and job.started_at is not None
    assert patreon_status(kit.claims, session_factory)["recent_starts"] == 1

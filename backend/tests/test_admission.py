"""Scan admission: whether a creator may be scanned now, asked when a scan is queued and again
when the worker starts it."""

from __future__ import annotations

import asyncio
import itertools

import pytest

from creeparr.core.errors import Conflict, NotFound
from creeparr.db.engine import session_scope
from creeparr.db.enums import AuthState, ScanMode, ScanTrigger
from creeparr.db.models import Creator
from creeparr.providers.errors import AuthError
from creeparr.scanner.admission import RefusalCode
from creeparr.services import build_services
from tests.test_lifecycle import RecordingBus

# Patreon starts with no session (blocked); YouTube needs none (never blocked); Instagram
# isn't registered in this version.
_ids = itertools.count(1000)


@pytest.fixture
def services(env, engine):
    services = build_services(env)
    services.scan_manager.bus = RecordingBus()
    return services


def add_creator(services, provider="youtube", *, enabled=True, monitored=True) -> int:
    with session_scope(services.session_factory) as s:
        c = Creator(campaign_id=str(next(_ids)), name=f"C{provider}", provider=provider)
        c.enabled, c.monitored = enabled, monitored
        s.add(c)
        s.flush()
        return c.id


def code(services, creator_id, trigger=ScanTrigger.MANUAL):
    refusal = services.scan_manager.admission.check(creator_id, trigger)
    return refusal and refusal.code


def skipped(services) -> list[tuple[int, str]]:
    return [
        (d["creator_id"], d["code"])
        for name, d in services.scan_manager.bus.events
        if name == "scan.skipped"
    ]


def test_a_creator_that_may_scan_is_admitted(services):
    assert code(services, add_creator(services)) is None


def test_the_first_rule_that_applies_is_the_reason(services):
    services.settings.update({"youtube": {"enabled": False}})
    off = {"enabled": False, "monitored": False}
    sched = ScanTrigger.SCHEDULE
    assert code(services, add_creator(services, "instagram", **off), sched) == (
        RefusalCode.PROVIDER_UNAVAILABLE
    )
    assert code(services, add_creator(services, "youtube", **off), sched) == (
        RefusalCode.PROVIDER_DISABLED
    )
    assert code(services, add_creator(services, "patreon", **off), sched) == (
        RefusalCode.CREATOR_DISABLED
    )
    assert code(services, add_creator(services, "patreon", monitored=False), sched) == (
        RefusalCode.NOT_MONITORED
    )
    assert code(services, add_creator(services, "patreon"), sched) == RefusalCode.AUTH_INVALID


def test_monitored_only_limits_automatic_scans(services):
    cid = add_creator(services, monitored=False)
    assert code(services, cid, ScanTrigger.MANUAL) is None
    assert code(services, cid, ScanTrigger.ADD) is None
    assert code(services, cid, ScanTrigger.SCAN_ALL) == RefusalCode.NOT_MONITORED
    assert code(services, cid, ScanTrigger.SCHEDULE) == RefusalCode.NOT_MONITORED


def test_candidates_are_exactly_the_creators_check_admits(services):
    ids = [
        add_creator(services, provider, enabled=enabled, monitored=monitored)
        for provider in ("youtube", "patreon", "instagram")
        for enabled in (True, False)
        for monitored in (True, False)
    ]
    for trigger in ScanTrigger:
        admitted = [cid for cid in ids if code(services, cid, trigger) is None]
        candidates = [c.creator_id for c in services.scan_manager.admission.candidates(trigger)]
        assert candidates == admitted, trigger


def test_a_refused_scan_is_a_conflict_with_the_reason(services):
    cid = add_creator(services, "instagram")
    with pytest.raises(Conflict) as exc:
        services.scan_manager.request_scan(cid)
    assert exc.value.code == "provider_unavailable"
    with pytest.raises(NotFound):
        services.scan_manager.request_scan(9999)


@pytest.mark.asyncio
async def test_an_unregistered_provider_does_not_stop_the_scheduled_passes(services):
    # A creator left over from a provider this version doesn't have, ahead of the others.
    gone = add_creator(services, "instagram")
    ok = [add_creator(services), add_creator(services)]
    assert await services.scheduler._scan_monitored() == {"queued": 2}
    assert [r["creator_id"] for r in services.scan_manager.status()["pending"]] == ok
    assert gone not in [r["creator_id"] for r in services.scan_manager.status()["pending"]]


@pytest.mark.asyncio
async def test_full_rescan_covers_only_admitted_creators_that_are_due(services):
    services.settings.update({"scan": {"full_rescan_days": 30}})
    blocked = add_creator(services, "patreon")
    off = add_creator(services, enabled=False)
    ok = add_creator(services)
    assert await services.scheduler._full_rescan() == {"queued": 1}
    assert services.scan_manager.status()["pending"] == [
        {"creator_id": ok, "mode": ScanMode.FULL, "trigger": ScanTrigger.SCHEDULE}
    ]
    assert {blocked, off}.isdisjoint(
        r["creator_id"] for r in services.scan_manager.status()["pending"]
    )


def test_scan_all_skips_unmonitored_creators(services):
    watched = add_creator(services)
    add_creator(services, monitored=False)
    assert services.scan_manager.request_scan_all() == 1
    assert services.scan_manager.status()["pending"] == [
        {"creator_id": watched, "mode": ScanMode.AUTO, "trigger": ScanTrigger.SCAN_ALL}
    ]


def test_adding_creators_reports_a_refused_first_scan_and_carries_on(services):
    refused = add_creator(services, "patreon")  # no session yet
    ok = add_creator(services)
    assert services.scan_manager.scan_new_creator(refused) is False
    assert services.scan_manager.scan_new_creator(ok) is True
    assert skipped(services) == [(refused, "auth_invalid")]
    assert [r["creator_id"] for r in services.scan_manager.status()["pending"]] == [ok]


class FakeScanner:
    """Records the scans the worker starts; a creator in `fail` hits an expired session."""

    def __init__(self, services, fail=()) -> None:
        self.services = services
        self.fail = set(fail)
        self.started: list[int] = []

    async def scan_creator(self, creator_id, mode, trigger, cancel):
        self.started.append(creator_id)
        if creator_id in self.fail:
            exc = AuthError("session expired")
            self.services.providers.get("patreon").mark_auth_invalid(exc)
            raise exc

    def close_interrupted_runs(self) -> None:
        pass


async def run_queue(services, scanner) -> None:
    manager = services.scan_manager
    manager.scanner = scanner
    await manager.start()
    try:
        await asyncio.wait_for(manager._queue.join(), timeout=5)
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_a_queued_scan_refused_by_the_time_it_starts_is_skipped(services):
    later_off, ok = add_creator(services), add_creator(services)
    for cid in (later_off, ok):
        services.scan_manager.request_scan(cid)
    with session_scope(services.session_factory) as s:
        s.get(Creator, later_off).enabled = False
    scanner = FakeScanner(services)
    await run_queue(services, scanner)
    assert scanner.started == [ok]
    assert skipped(services) == [(later_off, "creator_disabled")]


@pytest.mark.asyncio
async def test_an_expired_session_skips_that_providers_queued_scans_only(services):
    services.providers.get("patreon")._set_auth_status(AuthState.VALID)
    first, second = add_creator(services, "patreon"), add_creator(services, "patreon")
    other = add_creator(services)
    for cid in (first, second, other):
        services.scan_manager.request_scan(cid)
    scanner = FakeScanner(services, fail={first})
    await run_queue(services, scanner)
    assert scanner.started == [first, other]
    assert skipped(services) == [(second, "auth_invalid")]


@pytest.mark.asyncio
async def test_a_creator_deleted_while_queued_is_dropped(services):
    cid = add_creator(services)
    services.scan_manager.request_scan(cid)
    with session_scope(services.session_factory) as s:
        s.delete(s.get(Creator, cid))
    scanner = FakeScanner(services)
    await run_queue(services, scanner)
    assert scanner.started == [] and skipped(services) == []


def test_a_creator_deleted_mid_pass_only_skips_that_creator(services):
    first, ok = add_creator(services), add_creator(services)
    with session_scope(services.session_factory) as s:
        s.delete(s.get(Creator, first))
    queued = services.scan_manager.request_scans([first, ok], ScanMode.AUTO, ScanTrigger.SCHEDULE)
    assert queued == 1
    assert [r["creator_id"] for r in services.scan_manager.status()["pending"]] == [ok]

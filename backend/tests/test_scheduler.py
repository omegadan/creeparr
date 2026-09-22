"""Scheduled scan passes: one creator that can't scan must not stop the rest."""

from __future__ import annotations

import pytest

from creeparr.core.errors import Conflict
from creeparr.db.engine import session_scope
from creeparr.db.models import Creator
from creeparr.scheduler import SchedulerService


class FakeScanManager:
    def __init__(self, blocked: set[int]) -> None:
        self.blocked = blocked
        self.requested: list[tuple[int, str]] = []

    def request_scan(self, creator_id, mode, trigger="manual"):
        if creator_id in self.blocked:
            raise Conflict("Patreon session is not valid", code="auth_invalid")
        self.requested.append((creator_id, mode))
        return True


class FakeProviders:
    def __init__(self, disabled: set[str] = frozenset()) -> None:
        self._disabled = set(disabled)

    def disabled_names(self) -> set[str]:
        return self._disabled


def make_scheduler(settings, session_factory, scan_manager, providers) -> SchedulerService:
    class Services:  # minimal stand-in for what the scheduler reads
        pass

    services = Services()
    services.settings = settings
    services.session_factory = session_factory
    services.scan_manager = scan_manager
    services.providers = providers
    return SchedulerService(services)


def add_creators(session_factory, specs) -> list[int]:
    ids = []
    with session_scope(session_factory) as s:
        for i, (provider, enabled) in enumerate(specs):
            c = Creator(campaign_id=str(1000 + i), name=f"C{i}", provider=provider)
            c.enabled = enabled
            s.add(c)
            s.flush()
            ids.append(c.id)
    return ids


@pytest.mark.asyncio
async def test_scan_monitored_skips_conflicting_creator(settings, session_factory):
    # The first creator's provider session has expired; the others must still be scanned.
    a, b, c = add_creators(
        session_factory, [("patreon", True), ("patreon", True), ("youtube", True)]
    )
    sm = FakeScanManager(blocked={a})
    sched = make_scheduler(settings, session_factory, sm, FakeProviders())
    assert await sched._scan_monitored() == {"queued": 2}
    assert sorted(cid for cid, _ in sm.requested) == [b, c]


@pytest.mark.asyncio
async def test_full_rescan_skips_conflicts_and_disabled(settings, session_factory):
    settings.update({"scan": {"full_rescan_days": 30}})
    blocked, ok, off, off_provider = add_creators(
        session_factory,
        [("patreon", True), ("patreon", True), ("patreon", False), ("reddit", True)],
    )
    sm = FakeScanManager(blocked={blocked})
    sched = make_scheduler(settings, session_factory, sm, FakeProviders(disabled={"reddit"}))
    assert await sched._full_rescan() == {"queued": 1}
    assert [cid for cid, _ in sm.requested] == [ok]

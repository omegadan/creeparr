"""Scan admission: whether a creator may be scanned now, and if not, why.

Every path that starts a scan asks here (the Scan button, Scan all, the scheduled passes, a
creator just added), once when the scan is queued and again when the worker starts it. Whether
a scan is *due* is the scheduler's business, and whether one is already queued is the scan
manager's; this only answers "may it run".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select

from creeparr.core.errors import Conflict, NotFound
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import ScanTrigger
from creeparr.db.models import Creator
from creeparr.providers.registry import ProviderRegistry, unavailable_message


class RefusalCode(StrEnum):
    """Why a scan may not run, in the order the rules are checked: a switched-off provider
    or creator explains a refusal better than an expired session does."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"  # not registered in this version
    PROVIDER_DISABLED = "provider_disabled"
    CREATOR_DISABLED = "creator_disabled"
    NOT_MONITORED = "not_monitored"  # automatic scans only
    AUTH_INVALID = "auth_invalid"


@dataclass(frozen=True)
class Refusal:
    creator_id: int
    creator_name: str
    code: RefusalCode
    message: str

    def as_conflict(self) -> Conflict:
        return Conflict(self.message, code=self.code)


@dataclass(frozen=True)
class ScanCandidate:
    """A creator admission lets an automatic scan cover, with what the scheduler needs to
    decide whether a scan is due."""

    creator_id: int
    provider: str
    last_scan_at: datetime | None
    last_full_scan_at: datetime | None
    scan_interval_minutes: int | None


class ScanAdmission:
    def __init__(self, session_factory: SessionFactory, providers: ProviderRegistry) -> None:
        self._factory = session_factory
        self._providers = providers

    def check(self, creator_id: int, trigger: ScanTrigger) -> Refusal | None:
        """None if the creator may be scanned now; raises NotFound if it doesn't exist."""
        with session_scope(self._factory) as s:
            creator = s.get(Creator, creator_id)
            if creator is None:
                raise NotFound("creator not found")
            return self._refusal(creator, trigger)

    def candidates(self, trigger: ScanTrigger) -> list[ScanCandidate]:
        """Every creator `check` would admit for `trigger`, by the same rules."""
        with session_scope(self._factory) as s:
            creators = s.execute(select(Creator).order_by(Creator.id)).scalars().all()
            return [
                ScanCandidate(
                    c.id, c.provider, c.last_scan_at, c.last_full_scan_at, c.scan_interval_minutes
                )
                for c in creators
                if self._refusal(c, trigger) is None
            ]

    def _refusal(self, creator: Creator, trigger: ScanTrigger) -> Refusal | None:
        def refuse(code: RefusalCode, message: str) -> Refusal:
            return Refusal(creator.id, creator.name, code, message)

        if not self._providers.has(creator.provider):
            return refuse(RefusalCode.PROVIDER_UNAVAILABLE, unavailable_message(creator.provider))
        provider = self._providers.get(creator.provider)
        if not provider.enabled:
            return refuse(
                RefusalCode.PROVIDER_DISABLED, f"{provider.label} is disabled in Settings"
            )
        if not creator.enabled:
            return refuse(RefusalCode.CREATOR_DISABLED, "this creator is disabled")
        if trigger.automatic and not creator.monitored:
            return refuse(RefusalCode.NOT_MONITORED, "this creator isn't monitored")
        if provider.auth_blocked:
            return refuse(
                RefusalCode.AUTH_INVALID,
                f"{provider.label} session is not valid; fix it in Settings before scanning",
            )
        return None

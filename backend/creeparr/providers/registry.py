"""Lookup of provider services by name."""

from __future__ import annotations

from collections.abc import Iterator

from creeparr.core.errors import NotFound
from creeparr.db.models import Creator
from creeparr.providers.base import ProviderService

DEFAULT_PROVIDER = "patreon"


class ProviderRegistry:
    def __init__(self, services: list[ProviderService]) -> None:
        self._by_name: dict[str, ProviderService] = {s.name: s for s in services}

    def __iter__(self) -> Iterator[ProviderService]:
        return iter(self._by_name.values())

    def names(self) -> list[str]:
        return list(self._by_name)

    def has(self, name: str) -> bool:
        return name in self._by_name

    def get(self, name: str | None) -> ProviderService:
        key = name or DEFAULT_PROVIDER
        try:
            return self._by_name[key]
        except KeyError as exc:
            raise NotFound(f"unknown provider '{key}'") from exc

    def for_creator(self, creator: Creator) -> ProviderService:
        return self.get(creator.provider)

    def blocked_names(self) -> set[str]:
        """Providers whose session is invalid/unconfigured (native downloads must wait)."""
        return {p.name for p in self if p.auth_blocked}

    def disabled_names(self) -> set[str]:
        """Providers switched off entirely in settings."""
        return {p.name for p in self if not p.enabled}

    async def rebuild_all(self) -> None:
        for p in self:
            await p.rebuild()

    async def aclose_all(self) -> None:
        for p in self:
            await p.aclose()

    def write_cookie_files(self) -> None:
        for p in self:
            p.write_cookie_file()

    def describe_all(self) -> list[dict]:
        return [p.describe() for p in self]

"""The providers Creeparr has, in the order the UI lists them."""

from __future__ import annotations

from creeparr.providers.base import ProviderService
from creeparr.providers.onlyfans.provider import OnlyFansProvider
from creeparr.providers.patreon import PatreonProvider
from creeparr.providers.youtube import YouTubeProvider

PROVIDER_TYPES: tuple[type[ProviderService], ...] = (
    PatreonProvider,
    OnlyFansProvider,
    YouTubeProvider,
    # Instagram and Reddit are off for now: their bot protection blocks scans too
    # aggressively to be usable. Their code stays; re-list them here to bring them back.
)

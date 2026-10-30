"""The providers Creeparr has, in the order the UI lists them."""

from __future__ import annotations

from creeparr.providers.base import ProviderService
from creeparr.providers.instagram.provider import InstagramProvider
from creeparr.providers.onlyfans.provider import OnlyFansProvider
from creeparr.providers.patreon import PatreonProvider
from creeparr.providers.reddit.provider import RedditProvider
from creeparr.providers.youtube import YouTubeProvider

PROVIDER_TYPES: tuple[type[ProviderService], ...] = (
    PatreonProvider,
    OnlyFansProvider,
    YouTubeProvider,
    InstagramProvider,
    RedditProvider,
)

"""Patreon provider: wraps PatreonClient behind the ProviderService interface."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from creeparr.db.enums import MediaSource
from creeparr.patreon.client import PatreonClient
from creeparr.patreon.drm import probe_hls_drm
from creeparr.patreon.media_resolver import resolve_media
from creeparr.patreon.parsing import IncludedIndex, post_from_resource
from creeparr.providers.base import ProviderService
from creeparr.providers.cookies import CookieSet
from creeparr.providers.errors import ForbiddenError, NotConfigured, TransportUnavailable
from creeparr.providers.http import RateLimiter, build_transport
from creeparr.providers.media import MediaRef, MediaSession
from creeparr.providers.models import (
    CreatorInfo,
    MediaSpec,
    PostPage,
    PostResource,
    SubscriptionInfo,
    UserInfo,
)

log = logging.getLogger(__name__)


class PatreonProvider(ProviderService):
    name: ClassVar[str] = "patreon"
    label: ClassVar[str] = "Patreon"
    credential_fields: ClassVar[tuple[str, ...]] = ("session_id", "cookies_txt")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._client: PatreonClient | None = None

    # ---- client ----------------------------------------------------------------------

    def _build_client(self, credentials: dict[str, str] | None = None) -> PatreonClient:
        s = self.settings.get().patreon
        creds = credentials or {}
        cookies = CookieSet.from_settings(
            creds.get("session_id", s.session_id), creds.get("cookies_txt", s.cookies_txt)
        )
        try:
            transport = build_transport(s.http_backend, s.impersonate_target)
        except TransportUnavailable as exc:
            log.warning("%s; falling back to httpx", exc)
            transport = build_transport("httpx")
        return PatreonClient(
            transport,
            RateLimiter(s.requests_per_second, (s.random_delay_min, s.random_delay_max)),
            cookies,
            user_agent=s.user_agent,
        )

    @property
    def client(self) -> PatreonClient:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.get().patreon.session_id)

    async def rebuild(self) -> None:
        async with self._lock:
            old = self._client
            self._client = self._build_client()
            if old is not None:
                await old.aclose()
        self.write_cookie_file()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def write_cookie_file(self) -> None:
        cookies = self.client.cookies
        try:
            if cookies.is_configured:
                cookies.write_netscape(self.cookie_file)
            elif self.cookie_file.exists():
                self.cookie_file.unlink()
        except OSError as exc:
            log.warning("could not write cookie file: %s", exc)

    # ---- provider API ----------------------------------------------------------------

    async def fetch_user(self, credentials: dict[str, str] | None = None) -> UserInfo:
        if credentials is None:
            if not self.is_configured:
                raise NotConfigured("no session_id configured")
            return await self.client.get_current_user()
        client = self._build_client(credentials)
        try:
            return await client.get_current_user()
        finally:
            await client.aclose()

    async def resolve_creator(self, query: str) -> CreatorInfo:
        campaign_id = await self.client.resolve_campaign_id(query)
        return await self.client.get_campaign(campaign_id)

    async def get_creator(self, external_id: str) -> CreatorInfo:
        return await self.client.get_campaign(external_id)

    async def list_subscriptions(self) -> list[SubscriptionInfo]:
        return await self.client.get_pledges()

    def iter_posts(self, external_id: str) -> AsyncIterator[PostPage]:
        return self.client.iter_posts(external_id)

    async def get_post(
        self, external_id: str, post_id: str, *, post_type: str | None = None
    ) -> PostResource:
        return await self.client.get_post(post_id)

    def resolve_media(self, post: PostResource) -> list[MediaSpec]:
        return resolve_media(post)

    def post_from_raw(self, raw_json: dict[str, Any]) -> PostResource | None:
        data = raw_json.get("data")
        if not isinstance(data, dict):
            return None
        return post_from_resource(data, IncludedIndex({"included": raw_json.get("included") or []}))

    def media_session(self) -> MediaSession:
        s = self.settings.get().patreon
        client = self.client
        return MediaSession(
            headers=client.media_headers(),
            cookie=client.cookies.header(),
            cookie_domain="patreon.com",
            cookie_file=self.written_cookie_file,
            impersonate=s.impersonate_target if s.http_backend == "curl_cffi" else None,
            limiter=client.limiter,
        )

    async def drm_protected(self, media: MediaRef) -> bool:
        if await super().drm_protected(media):
            return True
        # Mux streams: the playlist says whether it's encrypted with a DRM key system.
        return media.source == MediaSource.NATIVE_HLS and await probe_hls_drm(
            self.client.fetch_text, media.url or ""
        )


__all__ = ["ForbiddenError", "PatreonProvider"]

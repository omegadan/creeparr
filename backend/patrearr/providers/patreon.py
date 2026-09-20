"""Patreon provider: wraps PatreonClient behind the ProviderService interface."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from patrearr.patreon.client import PatreonClient
from patrearr.patreon.cookies import CookieSet
from patrearr.patreon.media_resolver import resolve_media
from patrearr.patreon.parsing import IncludedIndex, post_from_resource
from patrearr.patreon.transport import RateLimiter, TransportResponse, build_transport
from patrearr.providers.base import ProviderService
from patrearr.providers.errors import ForbiddenError, NotConfigured, TransportUnavailable
from patrearr.providers.models import (
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
    supports_embeds: ClassVar[bool] = True

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

    @property
    def ytdlp_impersonate(self) -> bool:
        return self.settings.get().patreon.http_backend == "curl_cffi"

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

    async def get_post(self, external_id: str, post_id: str) -> PostResource:
        return await self.client.get_post(post_id)

    def resolve_media(self, post: PostResource) -> list[MediaSpec]:
        return resolve_media(post)

    def post_from_raw(self, raw_json: dict[str, Any]) -> PostResource | None:
        data = raw_json.get("data")
        if not isinstance(data, dict):
            return None
        return post_from_resource(data, IncludedIndex({"included": raw_json.get("included") or []}))

    def media_headers(self) -> dict[str, str]:
        return self.client.media_headers()

    async def fetch_text(self, url: str) -> str:
        return await self.client.fetch_text(url)

    async def stream(self, url: str, *, range_start: int = 0) -> TransportResponse:
        return await self.client.stream(
            url, range_start=range_start, headers=self.client.media_headers()
        )


__all__ = ["ForbiddenError", "PatreonProvider"]

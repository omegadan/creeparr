"""OnlyFans provider service."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any, ClassVar
from urllib.parse import urlparse

from creeparr.db.enums import MediaKind, MediaSource
from creeparr.providers.base import ProviderService
from creeparr.providers.cookies import CookieSet
from creeparr.providers.errors import NotConfigured, ProviderError, TransportFailure
from creeparr.providers.http import (
    RateLimiter,
    TransportResponse,
    build_transport,
    host_matches,
    with_range,
)
from creeparr.providers.models import (
    CreatorInfo,
    MediaSpec,
    PostPage,
    PostResource,
    SubscriptionInfo,
    UserInfo,
)
from creeparr.providers.onlyfans.client import (
    DEFAULT_UA,
    OnlyFansClient,
    OnlyFansCredentials,
    rebuild_post,
)
from creeparr.providers.onlyfans.signing import DynamicRules

log = logging.getLogger("creeparr.onlyfans")

DEFAULT_RULES_URL = (
    "https://raw.githubusercontent.com/DATAHOARDERS/dynamic-rules/main/onlyfans.json"
)
# Known-dead URLs that were shipped as defaults in earlier builds; silently upgraded.
DEAD_RULES_URLS = {
    "https://raw.githubusercontent.com/deviint/onlyfans-dynamic-rules/main/dynamicRules.json",
    "",
}

_KIND_MAP = {
    "video": MediaKind.VIDEO,
    "gif": MediaKind.VIDEO,
    "audio": MediaKind.AUDIO,
    "photo": MediaKind.IMAGE,
}


class OnlyFansProvider(ProviderService):
    name: ClassVar[str] = "onlyfans"
    label: ClassVar[str] = "OnlyFans"
    credential_fields: ClassVar[tuple[str, ...]] = ("sess", "auth_id", "x_bc", "cookies_txt")
    supports_embeds: ClassVar[bool] = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._client: OnlyFansClient | None = None
        self._rules: DynamicRules | None = None
        self._rules_fetched = 0.0

    # ---- settings / credentials ----------------------------------------------------

    def _creds(self, submitted: dict[str, str] | None = None) -> OnlyFansCredentials:
        s = self.settings.get().onlyfans
        c = submitted or {}
        return OnlyFansCredentials(
            sess=c.get("sess", s.sess),
            auth_id=c.get("auth_id", s.auth_id),
            x_bc=c.get("x_bc", s.x_bc),
            user_agent=s.user_agent or DEFAULT_UA,
            cookies_txt=c.get("cookies_txt", s.cookies_txt),
        )

    @property
    def is_configured(self) -> bool:
        return self._creds().is_configured

    @property
    def ytdlp_impersonate(self) -> bool:
        return self.settings.get().onlyfans.http_backend == "curl_cffi"

    def _transport(self):  # noqa: ANN202
        s = self.settings.get().onlyfans
        try:
            return build_transport(s.http_backend, s.impersonate_target)
        except ProviderError as exc:
            log.warning("%s; falling back to httpx", exc)
            return build_transport("httpx")

    async def _dynamic_rules(self) -> DynamicRules:
        if self._rules is not None and time.monotonic() - self._rules_fetched < 3600:
            return self._rules
        url = self.settings.get().onlyfans.dynamic_rules_url.strip()
        if url in DEAD_RULES_URLS:
            log.info("configured OnlyFans rules URL is empty/retired; using the default")
            url = DEFAULT_RULES_URL
        transport = build_transport("httpx")
        try:
            resp = await transport.request("GET", url)
            if resp.status != 200:
                raise ProviderError(f"rules URL returned HTTP {resp.status}")
            self._rules = DynamicRules.from_json(json.loads(resp.text))
            self._rules_fetched = time.monotonic()
            log.info("loaded OnlyFans dynamic signing rules from %s", url)
            return self._rules
        except Exception as exc:  # noqa: BLE001
            if self._rules is not None:
                log.warning("could not refresh OnlyFans rules (%s); using cached rules", exc)
                return self._rules
            raise ProviderError(
                f"could not load OnlyFans signing rules from {url}: {exc}. "
                "Set a working rules URL in Settings -> OnlyFans."
            ) from exc
        finally:
            await transport.aclose()

    async def _get_client(self, submitted: dict[str, str] | None = None) -> OnlyFansClient:
        creds = self._creds(submitted)
        if not creds.is_configured:
            raise NotConfigured("OnlyFans credentials are incomplete (need sess, auth_id, x_bc)")
        rules = await self._dynamic_rules()
        return OnlyFansClient(
            self._transport(), RateLimiter(self.group_settings().requests_per_second), creds, rules
        )

    async def _client_for_use(self) -> OnlyFansClient:
        if self._client is None:
            self._client = await self._get_client()
        return self._client

    async def rebuild(self) -> None:
        async with self._lock:
            old = self._client
            self._client = None
            self._rules = None  # the rules URL may have changed too
            if old is not None:
                await old._transport.aclose()
        self.write_cookie_file()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client._transport.aclose()
            self._client = None

    def write_cookie_file(self) -> None:
        creds = self._creds()
        try:
            if creds.is_configured:
                cs = CookieSet.from_settings(None, creds.cookies_txt, domain="onlyfans.com")
                cs.extra.update({"sess": creds.sess, "auth_id": creds.auth_id})
                cs.domain = "onlyfans.com"
                cs.write_netscape(self.cookie_file)
            elif self.cookie_file.exists():
                self.cookie_file.unlink()
        except OSError as exc:
            log.warning("could not write OnlyFans cookie file: %s", exc)

    # ---- provider API ----------------------------------------------------------------

    async def fetch_user(self, credentials: dict[str, str] | None = None) -> UserInfo:
        client = (
            await self._get_client(credentials) if credentials else await self._client_for_use()
        )
        return await client.get_me()

    async def resolve_creator(self, query: str) -> CreatorInfo:
        return await (await self._client_for_use()).resolve_creator(query)

    async def get_creator(self, external_id: str) -> CreatorInfo:
        return await (await self._client_for_use()).get_creator(external_id)

    async def list_subscriptions(self) -> list[SubscriptionInfo]:
        return await (await self._client_for_use()).list_subscriptions()

    async def iter_posts(self, external_id: str) -> AsyncIterator[PostPage]:
        client = await self._client_for_use()
        async for page in client.iter_posts(external_id):
            yield page

    async def _iter_archived(self, external_id: str) -> AsyncIterator[PostPage]:
        client = await self._client_for_use()
        async for page in client.iter_archived(external_id):
            yield page

    async def _iter_messages(self, external_id: str) -> AsyncIterator[PostPage]:
        client = await self._client_for_use()
        async for page in client.iter_messages(external_id):
            yield page

    def iter_sources(self, external_id: str) -> list[tuple[str, AsyncIterator[PostPage]]]:
        of = self.group_settings()
        sources: list[tuple[str, AsyncIterator[PostPage]]] = [
            ("posts", self.iter_posts(external_id))
        ]
        if of.include_archived:
            sources.append(("archived", self._iter_archived(external_id)))
        if of.include_messages:
            sources.append(("messages", self._iter_messages(external_id)))
        return sources

    async def get_post(self, external_id: str, post_id: str) -> PostResource:
        return await (await self._client_for_use()).get_post(external_id, post_id)

    def resolve_media(self, post: PostResource) -> list[MediaSpec]:
        if not post.current_user_can_view:
            return []
        specs: list[MediaSpec] = []
        counters: dict[MediaKind, int] = {}
        for m in post.media:
            meta = m.metadata or {}
            if not meta.get("can_view", True):
                continue
            url = m.best_url
            if not url:
                continue
            kind = _KIND_MAP.get(
                str(meta.get("of_type") or m.relationship).lower(), MediaKind.IMAGE
            )
            counters[kind] = counters.get(kind, 0) + 1
            ext = PurePosixPath(urlparse(url).path).suffix.lstrip(".").lower() or None
            specs.append(
                MediaSpec(
                    media_key=f"media:{m.id}",
                    kind=kind,
                    source=MediaSource.MEDIA_DOWNLOAD,
                    url=url,
                    file_name=f"{m.id}.{ext}" if ext else None,
                    metadata={"drm": bool(meta.get("drm"))},
                    order_index=counters[kind],
                )
            )
        return specs

    def post_from_raw(self, raw_json: dict[str, Any]) -> PostResource | None:
        return rebuild_post(raw_json)

    def media_headers(self) -> dict[str, str]:
        creds = self._creds()
        return {"User-Agent": creds.user_agent, "Referer": "https://onlyfans.com/"}

    async def fetch_text(self, url: str) -> str:
        transport = self._transport()
        try:
            resp = await transport.request("GET", url, headers=self.media_headers())
            return resp.text
        finally:
            await transport.aclose()

    async def stream(self, url: str, *, range_start: int = 0) -> TransportResponse:
        headers = with_range(self.media_headers(), range_start)
        # OnlyFans CDN URLs are pre-signed; only send cookies to onlyfans.com itself.
        if host_matches(url, "onlyfans.com"):
            headers["Cookie"] = self._creds().cookie_header()
        transport = self._transport()
        try:
            resp = await transport.request("GET", url, headers=headers, stream=True)
        except TransportFailure:
            await transport.aclose()
            raise
        resp.also_close(transport.aclose)  # this one-off client goes with the response
        return resp

"""Instagram provider service."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any, ClassVar
from urllib.parse import urlparse

from patrearr.db.enums import MediaKind, MediaSource
from patrearr.patreon.cookies import CookieSet, write_cookiefile
from patrearr.patreon.transport import HttpxTransport, TransportResponse
from patrearr.providers.base import ProviderService
from patrearr.providers.errors import NotConfigured, NotFoundError, TransportFailure
from patrearr.providers.instagram.client import (
    BASE,
    DEFAULT_UA,
    SOURCE_URLS,
    InstagramClient,
    InstagramCredentials,
    creator_from_items,
    group_into_posts,
)
from patrearr.providers.models import (
    CreatorInfo,
    MediaSpec,
    PostPage,
    PostResource,
    SubscriptionInfo,
    UserInfo,
)

log = logging.getLogger("patrearr.instagram")

VIDEO_EXT = {"mp4", "mov", "webm"}


class InstagramProvider(ProviderService):
    name: ClassVar[str] = "instagram"
    label: ClassVar[str] = "Instagram"
    credential_fields: ClassVar[tuple[str, ...]] = ("sessionid", "cookies_txt")
    supports_embeds: ClassVar[bool] = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._transport = HttpxTransport()

    # ---- credentials / config ------------------------------------------------------

    def _creds(self, submitted: dict[str, str] | None = None) -> InstagramCredentials:
        s = self.settings.get().instagram
        c = submitted or {}
        extra: dict[str, str] = {}
        cookies_txt = c.get("cookies_txt", s.cookies_txt)
        sid = c.get("sessionid", s.sessionid)
        if cookies_txt:
            cs = CookieSet.from_settings(
                None, cookies_txt, domain="instagram.com", session_name="sessionid"
            )
            extra = {k: v for k, v in cs.extra.items()}
            if not sid and cs.session_id:
                sid = cs.session_id
        return InstagramCredentials(
            sessionid=sid.strip(),
            user_agent=s.user_agent.strip() or DEFAULT_UA,
            extra_cookies=extra or None,
        )

    def _client(self, submitted: dict[str, str] | None = None) -> InstagramClient:
        return InstagramClient(self._creds(submitted), self.settings.get().instagram.sleep_request)

    @property
    def is_configured(self) -> bool:
        return self._creds().is_configured

    async def rebuild(self) -> None:
        self.write_cookie_file()

    async def aclose(self) -> None:
        await self._transport.aclose()

    def write_cookie_file(self) -> None:
        s = self.settings.get().instagram
        try:
            if s.cookies_txt.strip() and write_cookiefile(s.cookies_txt, self.cookie_file):
                return
            creds = self._creds()
            if creds.is_configured:
                cs = CookieSet(session_id=None, extra=creds.cookies(), domain="instagram.com")
                cs.write_netscape(self.cookie_file)
            elif self.cookie_file.exists():
                self.cookie_file.unlink()
        except OSError as exc:
            log.warning("could not write Instagram cookie file: %s", exc)

    # ---- provider API --------------------------------------------------------------

    @staticmethod
    def _username(query: str) -> str:
        q = query.strip().rstrip("/")
        if "instagram.com" in q:
            parts = [p for p in urlparse(q if "://" in q else f"https://{q}").path.split("/") if p]
            if parts and parts[0] == "stories":
                parts = parts[1:]
            if parts:
                return parts[0].lstrip("@")
        return q.lstrip("@")

    async def fetch_user(self, credentials: dict[str, str] | None = None) -> UserInfo:
        creds = self._creds(credentials)
        if not creds.is_configured:
            raise NotConfigured("no Instagram sessionid configured")
        # Verify the session by extracting our own account's feed metadata.
        client = InstagramClient(creds, self.settings.get().instagram.sleep_request)
        items = await asyncio.to_thread(client.extract, f"{BASE}/accounts/edit/")
        meta = items[0][1] if items else {}
        uid = str(meta.get("owner_id") or meta.get("id") or "instagram")
        return UserInfo(
            id=uid, full_name=meta.get("username") or "Instagram", vanity=meta.get("username")
        )

    async def resolve_creator(self, query: str) -> CreatorInfo:
        username = self._username(query)
        if not username:
            raise NotFoundError(f"could not read an Instagram username from '{query}'")
        client = self._client()
        items = await asyncio.to_thread(client.extract, f"{BASE}/{username}/")
        return creator_from_items(items[:1], username)

    async def get_creator(self, external_id: str) -> CreatorInfo:
        return await self.resolve_creator(external_id)

    async def list_subscriptions(self) -> list[SubscriptionInfo]:
        return []  # add Instagram accounts by URL/handle

    def _enabled_sources(self) -> list[str]:
        s = self.settings.get().instagram
        sources = ["posts"]
        if s.include_reels:
            sources.append("reels")
        if s.include_stories:
            sources.append("stories")
        if s.include_highlights:
            sources.append("highlights")
        if s.include_tagged:
            sources.append("tagged")
        return sources

    async def _iter_source(self, username: str, source: str) -> AsyncIterator[PostPage]:
        client = self._client()
        url = SOURCE_URLS[source].format(u=username)
        try:
            items = await asyncio.to_thread(client.extract, url)
        except NotFoundError:
            yield PostPage(posts=[], next_url=None, source=source)
            return
        posts = group_into_posts(items, source, username)
        max_posts = self.settings.get().instagram.max_posts
        if source == "posts" and max_posts and max_posts > 0:
            posts = posts[:max_posts]
        # gallery-dl returns the whole listing at once; chunk it into pages.
        page = 100
        if not posts:
            yield PostPage(posts=[], next_url=None, source=source)
            return
        for i in range(0, len(posts), page):
            more = i + page < len(posts)
            yield PostPage(
                posts=posts[i : i + page], next_url="more" if more else None, source=source
            )

    def iter_posts(self, external_id: str) -> AsyncIterator[PostPage]:
        return self._iter_source(external_id, "posts")

    def iter_sources(self, external_id: str) -> list[tuple[str, AsyncIterator[PostPage]]]:
        return [(src, self._iter_source(external_id, src)) for src in self._enabled_sources()]

    async def get_post(self, external_id: str, post_id: str) -> PostResource:
        client = self._client()
        items = await asyncio.to_thread(client.extract, f"{BASE}/p/{post_id}/")
        posts = group_into_posts(items, "posts", external_id)
        if not posts:
            raise NotFoundError(f"post {post_id} not found")
        return posts[0]

    def resolve_media(self, post: PostResource) -> list[MediaSpec]:
        specs: list[MediaSpec] = []
        counters: dict[MediaKind, int] = {}
        for m in post.media:
            url = m.best_url
            if not url:
                continue
            ext = PurePosixPath(urlparse(url).path).suffix.lstrip(".").lower()
            kind = (
                MediaKind.VIDEO
                if (m.metadata.get("is_video") or ext in VIDEO_EXT)
                else MediaKind.IMAGE
            )
            counters[kind] = counters.get(kind, 0) + 1
            specs.append(
                MediaSpec(
                    media_key=f"ig:{m.id}",
                    kind=kind,
                    source=MediaSource.MEDIA_DOWNLOAD,
                    url=url,
                    file_name=m.file_name,
                    order_index=counters[kind],
                )
            )
        return specs

    def post_from_raw(self, raw_json: dict[str, Any]) -> PostResource | None:
        data = raw_json.get("data")
        if not isinstance(data, dict):
            return None
        # Rebuild is best-effort from stored media; Instagram URLs expire, so a refresh
        # (get_post) is preferred. Reconstruct enough for re-resolution.
        from patrearr.providers.models import MediaResource

        media = []
        for m in raw_json.get("included") or []:
            if isinstance(m, dict) and m.get("url"):
                media.append(
                    MediaResource(
                        id=str(m.get("num")), relationship="media", download_url=m["url"], raw=m
                    )
                )
        pr = PostResource(
            id=str(data.get("shortcode")),
            title=str(data.get("shortcode")),
            post_type="instagram_post",
            url=f"{BASE}/p/{data.get('shortcode')}/",
            campaign_id=str(data.get("owner_id") or data.get("username") or ""),
            media=media,
            raw=data,
        )
        return pr

    def media_headers(self) -> dict[str, str]:
        return {"User-Agent": self._creds().user_agent, "Referer": f"{BASE}/"}

    async def fetch_text(self, url: str) -> str:
        resp = await self._transport.request("GET", url, headers=self.media_headers())
        return resp.text

    async def stream(self, url: str, *, range_start: int = 0) -> TransportResponse:
        headers = dict(self.media_headers())
        if range_start > 0:
            headers["Range"] = f"bytes={range_start}-"
        try:
            return await self._transport.request("GET", url, headers=headers, stream=True)
        except TransportFailure:
            raise

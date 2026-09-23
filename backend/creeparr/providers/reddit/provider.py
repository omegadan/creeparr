"""Reddit provider service (Reddit JSON API, optional OAuth app)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any, ClassVar
from urllib.parse import urlparse

from creeparr.db.enums import AuthState, MediaKind, MediaSource
from creeparr.patreon.transport import HttpxTransport, TransportResponse
from creeparr.providers.base import ProviderService
from creeparr.providers.errors import NotFoundError, TransportFailure
from creeparr.providers.models import (
    CreatorInfo,
    MediaSpec,
    PostPage,
    PostResource,
    SubscriptionInfo,
    UserInfo,
)
from creeparr.providers.reddit.client import (
    DEFAULT_UA,
    WWW,
    RedditClient,
    RedditCredentials,
    media_resource,
)

log = logging.getLogger("creeparr.reddit")


class RedditProvider(ProviderService):
    name: ClassVar[str] = "reddit"
    label: ClassVar[str] = "Reddit"
    credential_fields: ClassVar[tuple[str, ...]] = ("client_id", "client_secret")
    supports_embeds: ClassVar[bool] = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._transport = HttpxTransport()

    # ---- config --------------------------------------------------------------------

    def _creds(self) -> RedditCredentials:
        s = self.settings.get().reddit
        return RedditCredentials(
            user_agent=s.user_agent.strip() or DEFAULT_UA,
            client_id=s.client_id.strip(),
            client_secret=s.client_secret.strip(),
        )

    def _client(self) -> RedditClient:
        return RedditClient(self._creds(), self.settings.get().reddit.sleep_request)

    @property
    def is_configured(self) -> bool:
        return True  # public

    def get_auth_status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "state": AuthState.VALID,
            "checked_at": None,
            "user_name": "public",
            "error": None,
        }

    @property
    def auth_blocked(self) -> bool:
        return False

    async def rebuild(self) -> None:
        return None

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def fetch_user(self, credentials: dict[str, str] | None = None) -> UserInfo:
        return UserInfo(id="reddit", full_name="Reddit (public, no login required)")

    # ---- provider API --------------------------------------------------------------

    @staticmethod
    def _target(query: str) -> str:
        """Return 'r/<sub>' or 'u/<name>' from a URL, prefixed form, or bare word."""
        q = query.strip().rstrip("/")
        if "reddit.com" in q:
            parts = [p for p in urlparse(q if "://" in q else f"https://{q}").path.split("/") if p]
            if len(parts) > 1 and parts[0] == "r":
                return f"r/{parts[1]}"
            if len(parts) > 1 and parts[0] in ("u", "user"):
                return f"u/{parts[1]}"
            raise NotFoundError(f"not a subreddit or user URL: {query}")
        low = q.lower()
        if low.startswith("r/"):
            return f"r/{q[2:]}"
        if low.startswith("u/"):
            return f"u/{q[2:]}"
        if low.startswith("user/"):
            return f"u/{q[5:]}"
        return f"r/{q}"  # bare word defaults to a subreddit

    async def resolve_creator(self, query: str) -> CreatorInfo:
        target = self._target(query)
        kind, _, name = target.partition("/")
        profile = await self._client().fetch_profile(target)
        is_user = kind == "u"
        return CreatorInfo(
            external_id=target,
            name=target,
            handle=name,
            url=f"{WWW}/user/{name}/" if is_user else f"{WWW}/r/{name}/",
            avatar_url=(
                (profile.get("icon_img") or profile.get("community_icon") or "").split("?")[0]
                or None
            ),
            description=(
                profile.get("public_description")
                or (profile.get("subreddit") or {}).get("public_description")
            ),
            is_nsfw=bool(profile.get("over18")),
            owner_user_id=name,
            owner_name=target,
            raw={"target": target},
        )

    async def get_creator(self, external_id: str) -> CreatorInfo:
        return await self.resolve_creator(external_id)

    async def list_subscriptions(self) -> list[SubscriptionInfo]:
        return []

    async def iter_posts(self, external_id: str) -> AsyncIterator[PostPage]:
        try:
            posts = await self._client().fetch_posts(
                external_id, self.settings.get().reddit.max_posts
            )
        except NotFoundError:
            yield PostPage(posts=[], next_url=None, source="posts")
            return
        page = 100
        if not posts:
            yield PostPage(posts=[], next_url=None, source="posts")
            return
        for i in range(0, len(posts), page):
            more = i + page < len(posts)
            yield PostPage(
                posts=posts[i : i + page], next_url="more" if more else None, source="posts"
            )

    async def get_post(self, external_id: str, post_id: str) -> PostResource:
        # Reddit media URLs are stable enough that a targeted refetch is rarely needed;
        # re-list and find the post, else report gone.
        posts = await self._client().fetch_posts(external_id, 0)
        for p in posts:
            if p.id == post_id:
                return p
        raise NotFoundError(f"post {post_id} not found")

    def resolve_media(self, post: PostResource) -> list[MediaSpec]:
        specs: list[MediaSpec] = []
        counters: dict[MediaKind, int] = {}
        for m in post.media:
            url = m.best_url
            if not url:
                continue
            if m.metadata.get("is_video"):
                kind, source = MediaKind.VIDEO, MediaSource.EMBED_OTHER
                file_name = None
            else:
                ext = PurePosixPath(urlparse(url).path).suffix.lstrip(".").lower()
                kind = (
                    MediaKind.IMAGE
                    if ext in ("jpg", "jpeg", "png", "gif", "webp")
                    else MediaKind.VIDEO
                )
                source = MediaSource.MEDIA_DOWNLOAD
                file_name = f"{m.id.replace(':', '_')}.{ext}" if ext else None
            counters[kind] = counters.get(kind, 0) + 1
            specs.append(
                MediaSpec(
                    media_key=f"reddit:{m.id}",
                    kind=kind,
                    source=source,
                    url=url,
                    file_name=file_name,
                    order_index=counters[kind],
                )
            )
        return specs

    def post_from_raw(self, raw_json: dict[str, Any]) -> PostResource | None:
        data = raw_json.get("data")
        if not isinstance(data, dict):
            return None
        media = [
            media_resource(
                data.get("id"), m["url"], bool(m.get("is_video")), int(m.get("num") or 1)
            )
            for m in (raw_json.get("included") or [])
            if isinstance(m, dict) and m.get("url")
        ]
        return PostResource(
            id=str(data.get("id")),
            title=str(data.get("title") or data.get("id")),
            post_type="reddit_post",
            url=data.get("permalink") or f"{WWW}/comments/{data.get('id')}/",
            campaign_id=str(data.get("target") or ""),
            media=media,
            raw=data,
        )

    def media_headers(self) -> dict[str, str]:
        return {"User-Agent": self.settings.get().reddit.user_agent or DEFAULT_UA}

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

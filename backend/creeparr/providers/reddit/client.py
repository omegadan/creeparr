"""Reddit listing via Reddit's JSON API (with an optional OAuth app for reliability).

Public listings work anonymously from most home connections. If a Reddit "script" app
(client id + secret) is configured, requests use the authenticated API instead, which is
more reliable and less rate-limited. Images/galleries download directly; Reddit-hosted
and external videos are handed to yt-dlp by permalink so audio is merged.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from creeparr.providers.errors import (
    AuthError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
    TransportFailure,
)
from creeparr.providers.models import MediaResource, PostResource

log = logging.getLogger("creeparr.reddit")

WWW = "https://www.reddit.com"
OAUTH = "https://oauth.reddit.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


@dataclass
class RedditCredentials:
    user_agent: str = DEFAULT_UA
    client_id: str = ""
    client_secret: str = ""

    @property
    def has_oauth(self) -> bool:
        return bool(self.client_id and self.client_secret)


class RedditClient:
    def __init__(self, creds: RedditCredentials, sleep_request: float = 0.6) -> None:
        self.creds = creds
        self.sleep_request = sleep_request
        self._token: str | None = None
        self._token_expiry: float = 0.0

    def _headers(self, token: str | None) -> dict[str, str]:
        h = {"User-Agent": self.creds.user_agent or DEFAULT_UA, "Accept": "application/json"}
        if token:
            h["Authorization"] = f"bearer {token}"
        return h

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        try:
            resp = await client.post(
                f"{WWW}/api/v1/access_token",
                data={"grant_type": "client_credentials"},
                auth=(self.creds.client_id, self.creds.client_secret),
                headers={"User-Agent": self.creds.user_agent or DEFAULT_UA},
            )
        except httpx.HTTPError as exc:
            raise TransportFailure(str(exc)) from exc
        if resp.status_code in (401, 403):
            raise AuthError("Reddit rejected the app credentials (client id/secret)")
        if resp.status_code != 200:
            raise ProviderError(f"Reddit token error {resp.status_code}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._token

    async def _listing(self, path: str, after: str | None) -> dict[str, Any]:
        params = {"limit": "100", "raw_json": "1"}
        if after:
            params["after"] = after
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            token = await self._get_token(client) if self.creds.has_oauth else None
            base = OAUTH if token else WWW
            try:
                resp = await client.get(
                    f"{base}{path}", params=params, headers=self._headers(token)
                )
            except httpx.HTTPError as exc:
                raise TransportFailure(str(exc)) from exc
        if resp.status_code == 404:
            raise NotFoundError(f"{path} not found")
        if resp.status_code == 429:
            raise RateLimitedError(float(resp.headers.get("retry-after", "30")))
        if resp.status_code in (401, 403):
            raise AuthError(
                "Reddit blocked this request (403). Try configuring a Reddit app "
                "(client id/secret) in Settings, or a different network."
            )
        if resp.status_code != 200 or "json" not in resp.headers.get("content-type", ""):
            raise ProviderError(f"Reddit returned {resp.status_code} (blocked or not JSON)")
        return resp.json()

    async def fetch_posts(self, target: str, max_posts: int = 0) -> list[PostResource]:
        kind, _, name = target.partition("/")
        path = f"/user/{name}/submitted/" if kind == "u" else f"/r/{name}/new/"
        posts: list[PostResource] = []
        after: str | None = None
        while True:
            data = await self._listing(path, after)
            children = (data.get("data") or {}).get("children") or []
            if not children:
                break
            for child in children:
                pr = _post_from_json(child.get("data") or {}, target)
                if pr is not None:
                    posts.append(pr)
                    if max_posts and len(posts) >= max_posts:
                        return posts
            after = (data.get("data") or {}).get("after")
            if not after:
                break
        return posts

    async def fetch_profile(self, target: str) -> dict[str, Any]:
        kind, _, name = target.partition("/")
        path = f"/user/{name}/about/" if kind == "u" else f"/r/{name}/about/"
        data = await self._listing(path, None)
        return (data.get("data") or {}) if isinstance(data, dict) else {}


def _dt(created_utc: Any) -> datetime | None:
    if isinstance(created_utc, int | float):
        return datetime.fromtimestamp(created_utc, tz=UTC)
    return None


def _permalink(post: dict[str, Any]) -> str:
    p = post.get("permalink") or f"/comments/{post.get('id')}/"
    return p if p.startswith("http") else f"{WWW}{p}"


def _post_media(post: dict[str, Any]) -> list[MediaResource]:
    permalink = _permalink(post)
    media: list[MediaResource] = []

    def add(url: str, is_video: bool, num: int) -> None:
        media.append(
            MediaResource(
                id=f"{post.get('id')}:{num}",
                relationship="video" if is_video else "images",
                download_url=url,
                metadata={"num": num, "is_video": is_video},
                raw={"url": url, "is_video": is_video, "num": num},
            )
        )

    if post.get("is_video") and (post.get("media") or {}).get("reddit_video"):
        add(permalink, True, 1)  # yt-dlp merges audio+video from the post URL
        return media
    if post.get("is_gallery") and post.get("gallery_data") and post.get("media_metadata"):
        for i, item in enumerate(post["gallery_data"].get("items") or [], start=1):
            mm = post["media_metadata"].get(item.get("media_id"), {})
            s = mm.get("s") or {}
            url = s.get("u") or s.get("gif") or s.get("mp4")
            if url:
                add(url, mm.get("e") == "AnimatedImage" and "mp4" in s, i)
        return media

    url = post.get("url_overridden_by_dest") or post.get("url") or ""
    low = url.lower().split("?", 1)[0]
    hint = post.get("post_hint")
    if not url or post.get("is_self"):
        return media
    if low.endswith(IMAGE_EXT):
        add(url, False, 1)
    elif low.endswith(".gifv"):
        add(url[:-5] + ".mp4", True, 1)
    elif "v.redd.it" in url or hint in ("hosted:video", "rich:video"):
        add(permalink, True, 1)
    elif hint == "image":
        add(url, False, 1)
    else:
        # external host (redgifs, imgur, streamable, youtube, ...) -> best-effort via yt-dlp
        add(url, True, 1)
    return media


def _post_from_json(post: dict[str, Any], target: str) -> PostResource | None:
    if not post.get("id"):
        return None
    media = _post_media(post)
    if not media:
        return None  # text/link post with nothing to archive
    title = str(post.get("title") or "").strip()
    return PostResource(
        id=str(post["id"]),
        title=title[:200] or str(post["id"]),
        post_type="reddit_post",
        content=post.get("selftext") or None,
        url=_permalink(post),
        published_at=_dt(post.get("created_utc")),
        current_user_can_view=True,
        campaign_id=target,
        thumbnail_override=(
            post.get("thumbnail") if str(post.get("thumbnail", "")).startswith("http") else None
        ),
        media=media,
        raw={
            "id": post["id"],
            "title": title,
            "subreddit": post.get("subreddit"),
            "author": post.get("author"),
            "permalink": _permalink(post),
            "target": target,
            "_kind": "reddit",
        },
    )

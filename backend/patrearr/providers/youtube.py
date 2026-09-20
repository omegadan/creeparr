"""YouTube provider: lists and downloads a channel's videos via yt-dlp.

Public channels need no login. yt-dlp does the heavy lifting (channel listing and
download); dates come from the channel RSS feed and are backfilled exactly at
download time. An optional cookies.txt unlocks members-only / age-restricted videos.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, ClassVar
from xml.etree import ElementTree

import httpx
import yt_dlp

from patrearr.db.enums import AuthState, MediaKind, MediaSource
from patrearr.patreon.cookies import CookieSet
from patrearr.patreon.transport import TransportResponse
from patrearr.providers.base import ProviderService
from patrearr.providers.errors import NotFoundError, ProviderError
from patrearr.providers.models import (
    CreatorInfo,
    MediaSpec,
    PostPage,
    PostResource,
    SubscriptionInfo,
    UserInfo,
)

log = logging.getLogger("patrearr.youtube")

WATCH = "https://www.youtube.com/watch?v="
PAGE_SIZE = 100


def _best_thumb(thumbnails: list[dict] | None) -> str | None:
    if not thumbnails:
        return None
    best = max(
        thumbnails, key=lambda t: (t.get("height") or 0) * (t.get("width") or 0), default=None
    )
    return best.get("url") if best else None


def _parse_rss(xml: str) -> dict[str, datetime]:
    dates: dict[str, datetime] = {}
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return dates
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    for entry in root.findall("a:entry", ns):
        vid = entry.findtext("yt:videoId", namespaces=ns)
        published = entry.findtext("a:published", namespaces=ns)
        if vid and published:
            with contextlib.suppress(ValueError):
                dates[vid] = datetime.fromisoformat(published.replace("Z", "+00:00"))
    return dates


class YouTubeProvider(ProviderService):
    name: ClassVar[str] = "youtube"
    label: ClassVar[str] = "YouTube"
    credential_fields: ClassVar[tuple[str, ...]] = ("cookies_txt",)
    supports_embeds: ClassVar[bool] = True

    # ---- settings / auth -----------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return True  # public content needs no credentials

    def cookiefile_or_none(self) -> str | None:
        return str(self.cookie_file) if self.cookie_file.exists() else None

    async def rebuild(self) -> None:
        self.write_cookie_file()

    async def aclose(self) -> None:
        return None

    def write_cookie_file(self) -> None:
        cookies_txt = self.group_settings().cookies_txt
        try:
            if cookies_txt.strip():
                CookieSet.from_settings(None, cookies_txt).write_netscape(self.cookie_file)
            elif self.cookie_file.exists():
                self.cookie_file.unlink()
        except OSError as exc:
            log.warning("could not write YouTube cookie file: %s", exc)

    async def fetch_user(self, credentials: dict[str, str] | None = None) -> UserInfo:
        # No account concept; report a static "connected" identity.
        return UserInfo(id="youtube", full_name="YouTube (public, no login required)")

    def get_auth_status(self) -> dict[str, Any]:  # always usable
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

    # ---- yt-dlp helpers ------------------------------------------------------------

    def _ydl_opts(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True}
        cf = self.cookiefile_or_none()
        if cf:
            opts["cookiefile"] = cf
        if extra:
            opts.update(extra)
        return opts

    def _extract(self, url: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            with yt_dlp.YoutubeDL(self._ydl_opts(extra)) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            msg = str(exc).lower()
            if "not exist" in msg or "unavailable" in msg or "404" in msg:
                raise NotFoundError(str(exc)) from exc
            raise ProviderError(str(exc)) from exc
        if info is None:
            raise NotFoundError(f"nothing found at {url}")
        return info

    @staticmethod
    def _channel_videos_url(query: str) -> str:
        q = query.strip()
        if "youtube.com" in q or "youtu.be" in q:
            base = q.split("?")[0].rstrip("/")
            for tab in ("/videos", "/streams", "/shorts", "/featured", "/about"):
                if base.endswith(tab):
                    base = base[: -len(tab)]
            return base + "/videos"
        if q.startswith("UC") and len(q) >= 20:
            return f"https://www.youtube.com/channel/{q}/videos"
        handle = q.lstrip("@")
        return f"https://www.youtube.com/@{handle}/videos"

    def _creator_from_info(self, info: dict[str, Any]) -> CreatorInfo:
        channel_id = info.get("channel_id") or info.get("id")
        name = info.get("channel") or info.get("uploader") or info.get("title") or channel_id
        name = re.sub(r"\s*-\s*Videos$", "", str(name))
        return CreatorInfo(
            external_id=str(channel_id),
            name=name,
            handle=(info.get("uploader_id") or info.get("channel_id") or "").lstrip("@") or None,
            url=info.get("channel_url") or info.get("webpage_url"),
            avatar_url=_best_thumb(info.get("thumbnails")),
            description=info.get("description"),
            is_nsfw=False,
            owner_user_id=str(channel_id),
            owner_name=name,
            raw={
                k: info.get(k)
                for k in ("channel_id", "channel", "uploader", "uploader_id", "channel_url")
            },
        )

    # ---- provider API --------------------------------------------------------------

    async def resolve_creator(self, query: str) -> CreatorInfo:
        url = self._channel_videos_url(query)
        info = await asyncio.to_thread(
            self._extract, url, {"extract_flat": "in_playlist", "playlistend": 1}
        )
        if not (info.get("channel_id") or info.get("id")):
            raise NotFoundError(f"no YouTube channel for '{query}'")
        return self._creator_from_info(info)

    async def get_creator(self, external_id: str) -> CreatorInfo:
        return await self.resolve_creator(external_id)

    async def list_subscriptions(self) -> list[SubscriptionInfo]:
        return []  # importing subscriptions needs a Google login; add channels by URL

    def _post_from_entry(self, entry: dict[str, Any], channel_id: str) -> PostResource:
        vid = entry.get("id")
        ts = entry.get("timestamp")
        published = datetime.fromtimestamp(ts, tz=UTC) if ts else None
        return PostResource(
            id=str(vid),
            title=entry.get("title") or str(vid),
            post_type="youtube_video",
            url=entry.get("url") or f"{WATCH}{vid}",
            published_at=published,
            current_user_can_view=True,
            campaign_id=str(channel_id),
            thumbnail_override=_best_thumb(entry.get("thumbnails")),
            media=[],
            raw={
                "id": vid,
                "title": entry.get("title"),
                "url": entry.get("url"),
                "channel_id": channel_id,
                "duration": entry.get("duration"),
                "timestamp": ts,
                "_kind": "youtube",
            },
        )

    async def _rss_dates(self, channel_id: str) -> dict[str, datetime]:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
            return _parse_rss(resp.text) if resp.status_code == 200 else {}
        except httpx.HTTPError:
            return {}

    async def iter_posts(self, external_id: str) -> AsyncIterator[PostPage]:
        max_videos = self.group_settings().max_videos
        extra: dict[str, Any] = {"extract_flat": "in_playlist"}
        if max_videos and max_videos > 0:
            extra["playlistend"] = max_videos
        url = f"https://www.youtube.com/channel/{external_id}/videos"
        info = await asyncio.to_thread(self._extract, url, extra)
        entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
        rss = await self._rss_dates(external_id)
        for i in range(0, len(entries), PAGE_SIZE):
            chunk = entries[i : i + PAGE_SIZE]
            posts = []
            for e in chunk:
                pr = self._post_from_entry(e, external_id)
                if pr.published_at is None and pr.id in rss:
                    pr.published_at = rss[pr.id]
                    pr.raw["timestamp"] = rss[pr.id].timestamp()
                posts.append(pr)
            more = i + PAGE_SIZE < len(entries)
            yield PostPage(posts=posts, next_url="more" if more else None, source="videos")

    async def get_post(self, external_id: str, post_id: str) -> PostResource:
        info = await asyncio.to_thread(self._extract, f"{WATCH}{post_id}")
        published = None
        if info.get("timestamp"):
            published = datetime.fromtimestamp(info["timestamp"], tz=UTC)
        elif info.get("upload_date"):
            published = datetime.strptime(info["upload_date"], "%Y%m%d")
        return PostResource(
            id=str(info.get("id") or post_id),
            title=info.get("title") or post_id,
            post_type="youtube_video",
            content=info.get("description"),
            url=info.get("webpage_url") or f"{WATCH}{post_id}",
            published_at=published,
            current_user_can_view=True,
            campaign_id=str(info.get("channel_id") or external_id),
            thumbnail_override=info.get("thumbnail") or _best_thumb(info.get("thumbnails")),
            media=[],
            raw={
                "id": info.get("id"),
                "title": info.get("title"),
                "url": info.get("webpage_url"),
                "channel_id": info.get("channel_id"),
                "description": info.get("description"),
                "timestamp": info.get("timestamp"),
                "_kind": "youtube",
            },
        )

    def resolve_media(self, post: PostResource) -> list[MediaSpec]:
        url = post.url or f"{WATCH}{post.id}"
        return [
            MediaSpec(
                media_key=f"video:{post.id}",
                kind=MediaKind.VIDEO,
                source=MediaSource.EMBED_YOUTUBE,
                url=url,
                order_index=1,
            )
        ]

    def post_from_raw(self, raw_json: dict[str, Any]) -> PostResource | None:
        data = raw_json.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            return None
        return self._post_from_entry(data, str(data.get("channel_id") or ""))

    def media_headers(self) -> dict[str, str]:
        return {}

    async def fetch_text(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            return resp.text

    async def stream(self, url: str, *, range_start: int = 0) -> TransportResponse:
        raise ProviderError("YouTube media is downloaded via yt-dlp, not streamed directly")

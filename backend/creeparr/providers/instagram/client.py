"""Instagram listing via gallery-dl.

gallery-dl is used only to enumerate a profile's media (URLs + metadata); the files
themselves are downloaded through Creeparr's own pipeline. Instagram requires a
logged-in ``sessionid`` cookie and blocks aggressive automation, so requests are paced.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from creeparr.providers.errors import AuthError, NotFoundError, ProviderError
from creeparr.providers.models import CreatorInfo, MediaResource, PostResource

log = logging.getLogger("creeparr.instagram")

BASE = "https://www.instagram.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Source name -> URL template ({u} = username).
SOURCE_URLS = {
    "posts": f"{BASE}/{{u}}/",
    "reels": f"{BASE}/{{u}}/reels/",
    "tagged": f"{BASE}/{{u}}/tagged/",
    "highlights": f"{BASE}/{{u}}/highlights/",
    "stories": f"{BASE}/stories/{{u}}/",
}


@dataclass
class InstagramCredentials:
    sessionid: str
    user_agent: str = DEFAULT_UA
    extra_cookies: dict[str, str] | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.sessionid)

    def cookies(self) -> dict[str, str]:
        c = dict(self.extra_cookies or {})
        if self.sessionid:
            c["sessionid"] = self.sessionid
        return c


_GALLERY_DL_LOCK = threading.Lock()


class InstagramClient:
    """Thin wrapper over gallery-dl's DataJob (blocking; call inside a thread)."""

    def __init__(self, creds: InstagramCredentials, sleep_request: float = 1.0) -> None:
        self.creds = creds
        self.sleep_request = sleep_request

    def _apply_config(self) -> None:
        from gallery_dl import config

        config.clear()
        config.set(("extractor",), "cookies", self.creds.cookies())
        config.set(("extractor",), "user-agent", self.creds.user_agent)
        config.set(("extractor",), "sleep-request", self.sleep_request)
        # We only enumerate; never let gallery-dl write files.
        config.set(("extractor",), "skip", True)

    def extract(self, url: str) -> list[tuple[str, dict[str, Any]]]:
        """Return [(media_url, metadata), ...] for a profile/source/post URL."""
        if not self.creds.is_configured:
            raise AuthError("no Instagram sessionid configured")
        from gallery_dl import exception as gdl_exc
        from gallery_dl.job import DataJob

        # gallery-dl's config is process-wide and clients are created per call, so one
        # lock for all of them: a settings "Test" must not swap a running scan's cookies.
        with _GALLERY_DL_LOCK:
            self._apply_config()
            jobj = DataJob(url, file=None, resolve=True)
            jobj.run()
            exc = jobj.exception
            items = list(zip(jobj.data_urls, jobj.data_meta, strict=False))
        if exc is not None:
            name = exc.__class__.__name__
            msg = str(exc)
            if (
                isinstance(exc, gdl_exc.AuthenticationError)
                or "login" in msg.lower()
                or "403" in msg
            ):
                raise AuthError(f"Instagram rejected the session: {msg}")
            if isinstance(exc, gdl_exc.NotFoundError) or "not found" in msg.lower():
                raise NotFoundError(msg)
            raise ProviderError(f"{name}: {msg}")
        return items


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    return None


def _kind_ext(meta: dict[str, Any], url: str) -> str:
    ext = str(meta.get("extension") or "").lower()
    if ext:
        return ext
    tail = url.split("?", 1)[0].rsplit(".", 1)
    return tail[-1].lower() if len(tail) == 2 else ""


def media_resource(shortcode: str, url: str, meta: dict[str, Any]) -> MediaResource:
    """One media entry of a post, from gallery-dl metadata or from a stored raw entry.

    Scans and post_from_raw must build identical ids, since the id becomes the
    media_key that matches rows on re-resolution.
    """
    num = int(meta.get("num") or 1)
    ext = _kind_ext(meta, url)
    is_video = (
        bool(meta.get("is_video"))
        or bool(meta.get("video_url"))
        or "GraphVideo" in str(meta.get("typename", ""))
        or ext in ("mp4", "mov")
    )
    return MediaResource(
        id=f"{shortcode}:{num}",
        relationship="video" if is_video else "images",
        file_name=f"{shortcode}_{num}.{ext}" if ext else None,
        download_url=url,
        mimetype=None,
        metadata={"num": num, "typename": meta.get("typename"), "is_video": is_video},
        raw={
            "url": url,
            "is_video": is_video,
            "extension": ext or None,
            **{k: meta.get(k) for k in ("num", "typename", "date")},
        },
    )


def group_into_posts(
    items: list[tuple[str, dict[str, Any]]], source: str, username: str
) -> list[PostResource]:
    """Group gallery-dl media entries by their Instagram post."""
    posts: dict[str, PostResource] = {}
    order: list[str] = []
    for url, meta in items:
        if not url or not isinstance(meta, dict):
            continue
        shortcode = str(
            meta.get("post_shortcode") or meta.get("shortcode") or meta.get("post_id") or url
        )
        media = media_resource(shortcode, url, meta)
        is_video = bool(media.metadata["is_video"])
        if shortcode not in posts:
            order.append(shortcode)
            caption = str(meta.get("description") or "").strip()
            posts[shortcode] = PostResource(
                id=shortcode,
                title=caption[:200] or f"{source} {shortcode}",
                post_type=f"instagram_{source.rstrip('s')}"
                if source != "posts"
                else "instagram_post",
                content=caption or None,
                url=f"{BASE}/p/{shortcode}/",
                published_at=_dt(meta.get("date")),
                current_user_can_view=True,
                campaign_id=str(meta.get("owner_id") or username),
                thumbnail_override=url if not is_video else None,
                media=[],
                raw={
                    "shortcode": shortcode,
                    "source": source,
                    "username": username,
                    "owner_id": meta.get("owner_id"),
                    "_kind": "instagram",
                },
            )
        posts[shortcode].media.append(media)
    return [posts[s] for s in order]


def creator_from_items(items: list[tuple[str, dict[str, Any]]], username: str) -> CreatorInfo:
    meta = items[0][1] if items else {}
    return CreatorInfo(
        external_id=username,
        name=str(meta.get("fullname") or username),
        handle=username,
        url=f"{BASE}/{username}/",
        avatar_url=meta.get("avatar") or meta.get("profile_pic_url"),
        description=None,
        is_nsfw=None,
        owner_user_id=str(meta.get("owner_id")) if meta.get("owner_id") else None,
        owner_name=str(meta.get("fullname") or username),
        raw={"username": username, "owner_id": meta.get("owner_id")},
    )

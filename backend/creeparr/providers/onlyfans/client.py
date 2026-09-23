"""Minimal client for OnlyFans' private API."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from creeparr.patreon.transport import (
    RateLimiter,
    Transport,
    TransportResponse,
    parse_retry_after,
)
from creeparr.providers.errors import (
    AuthError,
    ForbiddenError,
    NotConfigured,
    NotFoundError,
    RateLimitedError,
    TransientError,
    UnexpectedResponse,
)
from creeparr.providers.models import (
    CreatorInfo,
    MediaResource,
    PostPage,
    PostResource,
    SubscriptionInfo,
    UserInfo,
)
from creeparr.providers.onlyfans.signing import DynamicRules, sign_request

log = logging.getLogger("creeparr.onlyfans")

API = "https://onlyfans.com/api2/v2"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class OnlyFansCredentials:
    def __init__(self, sess: str, auth_id: str, x_bc: str, user_agent: str, cookies_txt: str = ""):
        self.sess = sess.strip()
        self.auth_id = auth_id.strip()
        self.x_bc = x_bc.strip()
        self.user_agent = user_agent.strip() or DEFAULT_UA
        self.cookies_txt = cookies_txt

    @property
    def is_configured(self) -> bool:
        return bool(self.sess and self.auth_id and self.x_bc)

    def cookie_header(self) -> str:
        # OnlyFans expects auth_id, sess and auth_uid_ (defaults to auth_id).
        return f"auth_id={self.auth_id}; sess={self.sess}; auth_uid_={self.auth_id}"


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class OnlyFansClient:
    def __init__(
        self,
        transport: Transport,
        rate_limiter: RateLimiter,
        creds: OnlyFansCredentials,
        rules: DynamicRules,
    ) -> None:
        self._transport = transport
        self._limiter = rate_limiter
        self.creds = creds
        self.rules = rules

    def _headers(self, url: str) -> dict[str, str]:
        signed = sign_request(url, self.rules, self.creds.auth_id)
        return {
            "accept": "application/json, text/plain, */*",
            "app-token": self.rules.app_token,
            "user-agent": self.creds.user_agent,
            "x-bc": self.creds.x_bc,
            "user-id": self.creds.auth_id,
            "referer": "https://onlyfans.com",
            "cookie": self.creds.cookie_header(),
            **signed,
        }

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        if not self.creds.is_configured:
            raise NotConfigured("OnlyFans credentials are incomplete")
        url = path if path.startswith("http") else f"{API}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"
        await self._limiter.wait()
        resp = await self._transport.request("GET", url, headers=self._headers(url))
        log.debug("GET %s -> %s (%s)", path, resp.status, resp.content_type)
        return self._parse(resp)

    def _parse(self, resp: TransportResponse) -> Any:
        status = resp.status
        if status == 401:
            raise AuthError("OnlyFans rejected the session (401)")
        if status == 403:
            raise ForbiddenError("OnlyFans returned 403 (session, or no access)")
        if status == 404:
            raise NotFoundError("not found")
        if status == 429:
            raise RateLimitedError(parse_retry_after(resp.headers.get("retry-after")))
        if status >= 500:
            raise TransientError(f"server error {status}")
        try:
            data = json.loads(resp.text)
        except ValueError as exc:
            if status >= 400:
                raise UnexpectedResponse(f"OnlyFans returned {status}") from exc
            raise UnexpectedResponse("invalid JSON from OnlyFans") from exc
        # Any other failure (400 "Please refresh the page", or an error body on a 200)
        # must not read as an empty list, which would end a scan as if it succeeded.
        err = data.get("error") if isinstance(data, dict) else None
        if status >= 400 or (err and "list" not in data and "id" not in data):
            msg = (err.get("message") if isinstance(err, dict) else err) or f"HTTP {status}"
            low = str(msg).lower()
            if "wrong user" in low or "refresh" in low or "log in" in low or "login" in low:
                raise AuthError(f"OnlyFans rejected the session: {msg}")
            raise UnexpectedResponse(f"OnlyFans error ({status}): {msg}")
        return data

    async def get_me(self) -> UserInfo:
        data = await self._get("/users/me")
        if not isinstance(data, dict) or not data.get("id"):
            err = data.get("error") if isinstance(data, dict) else None
            msg = (err or {}).get("message") if isinstance(err, dict) else None
            log.warning("OnlyFans /users/me returned no account: %s", err)
            if msg and "wrong user" in msg.lower():
                raise AuthError("OnlyFans: 'Wrong user' — auth_id does not match the sess cookie")
            if msg and "refresh" in msg.lower():
                raise AuthError(
                    "OnlyFans: 'Please refresh the page' — the sess cookie or x-bc is stale; "
                    "re-copy them from a fresh onlyfans.com session"
                )
            raise AuthError(f"OnlyFans did not return an account ({msg or 'unknown'})")
        return UserInfo(
            id=str(data["id"]),
            full_name=data.get("name") or data.get("username"),
            vanity=data.get("username"),
            image_url=data.get("avatar"),
        )

    @staticmethod
    def _creator_from_user(data: dict[str, Any]) -> CreatorInfo:
        return CreatorInfo(
            external_id=str(data.get("id")),
            name=data.get("name") or data.get("username") or str(data.get("id")),
            handle=data.get("username"),
            url=f"https://onlyfans.com/{data.get('username')}" if data.get("username") else None,
            avatar_url=data.get("avatar"),
            cover_url=(data.get("header") or None),
            description=data.get("about") or None,
            is_nsfw=True,
            owner_user_id=str(data.get("id")),
            owner_name=data.get("name"),
            raw=data,
        )

    async def resolve_creator(self, query: str) -> CreatorInfo:
        handle = query.strip().rstrip("/").split("/")[-1].lstrip("@")
        data = await self._get(f"/users/{handle}")
        if not isinstance(data, dict) or not data.get("id"):
            raise NotFoundError(f"no OnlyFans creator '{query}'")
        return self._creator_from_user(data)

    async def get_creator(self, external_id: str) -> CreatorInfo:
        return await self.resolve_creator(external_id)

    async def list_subscriptions(self) -> list[SubscriptionInfo]:
        out: list[SubscriptionInfo] = []
        offset = 0
        while True:
            batch = await self._get(
                "/subscriptions/subscribes",
                {"offset": str(offset), "limit": "50", "type": "active"},
            )
            if not isinstance(batch, list) or not batch:
                break
            for u in batch:
                out.append(
                    SubscriptionInfo(
                        external_id=str(u.get("id")),
                        name=u.get("name") or u.get("username") or str(u.get("id")),
                        handle=u.get("username"),
                        url=f"https://onlyfans.com/{u.get('username')}"
                        if u.get("username")
                        else None,
                        avatar_url=u.get("avatar"),
                        is_free=(u.get("subscribePrice") or 0) == 0,
                        raw=u,
                    )
                )
            if len(batch) < 50:
                break
            offset += 50
        return out

    async def _iter_timeline_like(
        self, path: str, kind: str, source: str
    ) -> AsyncIterator[PostPage]:
        limit = 50
        params = {"limit": str(limit), "order": "publish_date_desc", "skip_users": "all"}
        before: str | None = None
        seen = 0
        while True:
            page_params = dict(params)
            if before:
                page_params["beforePublishTime"] = before
            data = await self._get(path, page_params)
            items = data.get("list") if isinstance(data, dict) else data
            if not isinstance(items, list) or not items:
                yield PostPage(posts=[], next_url=None, source=source)
                return
            has_more_flag = data.get("hasMore") if isinstance(data, dict) else None
            last = items[-1]
            next_before = last.get("postedAtPrecise") or last.get("postedAt")
            seen += len(items)
            log.debug("OnlyFans %s page: %d items (total %d)", kind, len(items), seen)
            posts = [self._post_from_json(p) for p in items]
            more_flag = has_more_flag if has_more_flag is not None else len(items) >= limit
            more = bool(more_flag and next_before)
            before = str(next_before) if next_before is not None else None
            yield PostPage(posts=posts, next_url="more" if more else None, source=source)
            if not more:
                return

    def iter_posts(self, external_id: str) -> AsyncIterator[PostPage]:
        return self._iter_timeline_like(f"/users/{external_id}/posts", "posts", "posts")

    def iter_archived(self, external_id: str) -> AsyncIterator[PostPage]:
        return self._iter_timeline_like(
            f"/users/{external_id}/posts/archived", "archived", "archived"
        )

    async def iter_messages(self, external_id: str) -> AsyncIterator[PostPage]:
        limit = 50
        before_id: str | None = None
        seen = 0
        while True:
            params = {"limit": str(limit), "order": "desc", "skip_users": "all"}
            if before_id:
                params["id"] = before_id
            data = await self._get(f"/chats/{external_id}/messages", params)
            items = data.get("list") if isinstance(data, dict) else data
            if not isinstance(items, list) or not items:
                yield PostPage(posts=[], next_url=None, source="messages")
                return
            has_more = bool(data.get("hasMore")) if isinstance(data, dict) else False
            seen += len(items)
            log.debug("OnlyFans messages page: %d items (total %d)", len(items), seen)
            posts = [self._message_from_json(m, external_id) for m in items]
            last_id = items[-1].get("id")
            before_id = str(last_id) if last_id is not None else None
            more = bool(has_more and before_id)
            yield PostPage(posts=posts, next_url="more" if more else None, source="messages")
            if not more:
                return

    async def get_post(self, external_id: str, post_id: str) -> PostResource:
        if post_id.startswith("msg-"):
            # Single messages cannot be re-fetched cheaply; signal no-refresh.
            raise NotFoundError("message media cannot be refreshed individually")
        data = await self._get(f"/posts/{post_id}", {"skip_users": "all"})
        if not isinstance(data, dict) or not data.get("id"):
            raise NotFoundError(f"post {post_id} not found")
        return self._post_from_json(data)

    @staticmethod
    def _thumb_url(item: dict[str, Any]) -> str | None:
        for m in item.get("media") or []:
            if not isinstance(m, dict):
                continue
            files = m.get("files") or {}
            for key in ("preview", "squarePreview", "thumb"):
                v = files.get(key)
                if isinstance(v, dict) and v.get("url"):
                    return str(v["url"])
        return None

    @staticmethod
    def _media_list(item: dict[str, Any]) -> list[MediaResource]:
        media: list[MediaResource] = []
        for m in item.get("media") or []:
            if not isinstance(m, dict):
                continue
            files = m.get("files") or {}
            full = files.get("full") or files.get("source") or {}
            src = m.get("source") or {}
            url = full.get("url") or src.get("source") or src.get("url")
            drm = bool(files.get("drm") or full.get("drm") or m.get("hasDrm"))
            media.append(
                MediaResource(
                    id=str(m.get("id")),
                    relationship=str(m.get("type") or "media"),
                    download_url=url,
                    metadata={
                        "of_type": m.get("type"),
                        "can_view": m.get("canView", True),
                        "drm": drm,
                        "duration": m.get("duration"),
                    },
                    raw=m,
                )
            )
        return media

    @classmethod
    def _post_from_json(cls, p: dict[str, Any]) -> PostResource:
        return PostResource(
            id=str(p.get("id")),
            title=(p.get("text") or "").strip()[:200] or f"Post {p.get('id')}",
            post_type="onlyfans_post",
            content=p.get("rawText") or p.get("text"),
            url=f"https://onlyfans.com/{p.get('id')}",
            published_at=_parse_dt(p.get("postedAt")),
            current_user_can_view=bool(p.get("canViewMedia", True)),
            campaign_id=str((p.get("author") or {}).get("id") or ""),
            media=cls._media_list(p),
            thumbnail_override=cls._thumb_url(p),
            raw=p,
        )

    @classmethod
    def _message_from_json(cls, m: dict[str, Any], creator_id: str) -> PostResource:
        media_items = m.get("media") or []
        if media_items:
            can_view = not m.get("canPurchase", False) and any(
                md.get("canView", True) for md in media_items
            )
        else:
            can_view = True
        return PostResource(
            id=f"msg-{m.get('id')}",
            title=(m.get("text") or "").strip()[:200] or f"Message {m.get('id')}",
            post_type="onlyfans_message",
            content=m.get("text"),
            url=f"https://onlyfans.com/my/chats/chat/{creator_id}",
            published_at=_parse_dt(m.get("createdAt")),
            current_user_can_view=bool(can_view),
            campaign_id=str(creator_id),
            media=cls._media_list(m),
            thumbnail_override=cls._thumb_url(m),
            raw={**m, "_kind": "message", "_creator_id": creator_id},
        )


def rebuild_post(raw: dict[str, Any]) -> PostResource | None:
    data = raw.get("data")
    if not isinstance(data, dict):
        return None
    if data.get("_kind") == "message":
        return OnlyFansClient._message_from_json(data, str(data.get("_creator_id") or ""))
    return OnlyFansClient._post_from_json(data)

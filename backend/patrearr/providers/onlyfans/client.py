"""Minimal client for OnlyFans' private API."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from patrearr.patreon.transport import RateLimiter, Transport, TransportResponse
from patrearr.providers.errors import (
    AuthError,
    ForbiddenError,
    NotConfigured,
    NotFoundError,
    RateLimitedError,
    TransientError,
    UnexpectedResponse,
)
from patrearr.providers.models import (
    CreatorInfo,
    MediaResource,
    PostPage,
    PostResource,
    SubscriptionInfo,
    UserInfo,
)
from patrearr.providers.onlyfans.signing import DynamicRules, sign_request

log = logging.getLogger("patrearr.onlyfans")

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
        return f"sess={self.sess}; auth_id={self.auth_id}"


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
            "referer": "https://onlyfans.com/",
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
            raise RateLimitedError(float(resp.headers.get("retry-after", "30")))
        if status >= 500:
            raise TransientError(f"server error {status}")
        try:
            return json.loads(resp.text)
        except ValueError as exc:
            raise UnexpectedResponse("invalid JSON from OnlyFans") from exc

    async def get_me(self) -> UserInfo:
        data = await self._get("/users/me")
        if not isinstance(data, dict) or not data.get("id"):
            raise AuthError("OnlyFans did not return an account for this session")
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

    async def iter_posts(self, external_id: str) -> AsyncIterator[PostPage]:
        params = {"limit": "50", "order": "publish_date_desc", "skip_users": "all"}
        before: str | None = None
        while True:
            page_params = dict(params)
            if before:
                page_params["beforePublishTime"] = before
            data = await self._get(f"/users/{external_id}/posts", page_params)
            items = data.get("list") if isinstance(data, dict) else data
            if not isinstance(items, list) or not items:
                yield PostPage(posts=[], next_url=None)
                return
            posts = [self._post_from_json(p) for p in items]
            has_more = bool(isinstance(data, dict) and data.get("hasMore"))
            before = str(items[-1].get("postedAtPrecise") or items[-1].get("postedAt") or "")
            yield PostPage(posts=posts, next_url="more" if has_more and before else None)
            if not has_more or not before:
                return

    async def get_post(self, external_id: str, post_id: str) -> PostResource:
        data = await self._get(f"/posts/{post_id}", {"skip_users": "all"})
        if not isinstance(data, dict) or not data.get("id"):
            raise NotFoundError(f"post {post_id} not found")
        return self._post_from_json(data)

    @staticmethod
    def _post_from_json(p: dict[str, Any]) -> PostResource:
        media: list[MediaResource] = []
        for m in p.get("media") or []:
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
                    mimetype=None,
                    metadata={
                        "of_type": m.get("type"),
                        "can_view": m.get("canView", True),
                        "drm": drm,
                        "duration": m.get("duration"),
                    },
                    raw=m,
                )
            )
        return PostResource(
            id=str(p.get("id")),
            title=(p.get("text") or "").strip()[:200] or f"Post {p.get('id')}",
            post_type="onlyfans_post",
            content=p.get("rawText") or p.get("text"),
            url=(f"https://onlyfans.com/{p.get('id')}").rstrip("/"),
            published_at=_parse_dt(p.get("postedAt")),
            current_user_can_view=bool(p.get("canViewMedia", True)),
            campaign_id=str((p.get("author") or {}).get("id") or ""),
            media=media,
            raw=p,
        )


def rebuild_post(raw: dict[str, Any]) -> PostResource | None:
    data = raw.get("data")
    if not isinstance(data, dict):
        return None
    return OnlyFansClient._post_from_json(data)

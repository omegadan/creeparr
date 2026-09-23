"""Client for Patreon's private JSON:API.

Everything that talks to patreon.com goes through here so that headers, cookies,
rate limiting and error classification live in one place. yt-dlp only ever receives
media/embed URLs, never API calls.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

from creeparr.patreon.errors import (
    AuthError,
    CloudflareChallengeError,
    ForbiddenError,
    NotFoundError,
    PatreonError,
    RateLimitedError,
    TransientError,
    TransportFailure,
    UnexpectedResponse,
)
from creeparr.patreon.media_resolver import is_patreon_url
from creeparr.patreon.parsing import (
    IncludedIndex,
    campaign_from_resource,
    extract_bootstrap_campaign_id,
    post_from_resource,
)
from creeparr.providers.cookies import CookieSet
from creeparr.providers.http import (
    RateLimiter,
    Transport,
    TransportResponse,
    parse_retry_after,
    with_range,
)
from creeparr.providers.models import (
    CreatorInfo,
    PostPage,
    PostResource,
    SubscriptionInfo,
    UserInfo,
)

log = logging.getLogger(__name__)

BASE_URL = "https://www.patreon.com"
API_URL = f"{BASE_URL}/api"
DEFAULT_USER_AGENT = "Patreon/126.9.0.15 (Android; Android 14; Scale/2.10)"

POST_INCLUDES = "campaign,access_rules,attachments_media,audio,images,media,user,user_defined_tags"
POST_FIELDS = ",".join(
    [
        "title", "content", "teaser_text", "published_at", "edited_at", "url", "patreon_url",
        "post_type", "current_user_can_view", "embed", "post_file", "image", "thumbnail",
        "thumbnail_url", "video_preview", "post_metadata", "is_paid", "min_cents_pledged_to_view",
        "meta_image_url", "upgrade_url", "pledge_url", "change_visibility_at",
    ]
)  # fmt: skip
CAMPAIGN_FIELDS = (
    "name,vanity,url,avatar_photo_url,avatar_photo_image_urls,cover_photo_url,"
    "creation_name,is_nsfw,published_at,summary"
)
USER_FIELDS = "full_name,url,image_url,vanity"
PLEDGE_CAMPAIGN_FIELDS = (
    "avatar_photo_image_urls,name,published_at,url,vanity,is_nsfw,url_for_current_user"
)
MEMBER_FIELDS = "is_free_member,is_free_trial"

# Path segments on patreon.com that are never a creator vanity.
RESERVED_PATHS = {
    "posts", "user", "c", "cw", "login", "join", "home", "explore", "settings", "messages",
    "api", "checkout", "create", "search", "collection", "collections", "shop", "product",
}  # fmt: skip

_CF_MARKERS = ("cf-chl", "challenge-platform", "Just a moment", "cf_chl_opt", "__cf_chl")


def _post_query() -> dict[str, str]:
    return {
        "include": POST_INCLUDES,
        "fields[post]": POST_FIELDS,
        "fields[user]": USER_FIELDS,
        "fields[campaign]": "name,vanity,url",
        "json-api-version": "1.0",
        "json-api-use-default-includes": "false",
    }


def looks_like_cloudflare(resp: TransportResponse) -> bool:
    if resp.headers.get("cf-mitigated") == "challenge":
        return True
    if "text/html" not in resp.content_type:
        return False
    body = resp.text[:20000]
    return any(marker in body for marker in _CF_MARKERS)


def _error_detail(resp: TransportResponse) -> str | None:
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return None
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, list) and errors:
        e = errors[0]
        if isinstance(e, dict):
            return str(e.get("detail") or e.get("title") or e.get("code_name") or e)
    return None


def classify_response(resp: TransportResponse, *, expect_json: bool = True) -> None:
    """Raise the appropriate PatreonError for a non-successful response."""
    status = resp.status
    if 200 <= status < 300:
        if expect_json and "json" not in resp.content_type:
            if looks_like_cloudflare(resp):
                raise CloudflareChallengeError("Cloudflare challenge page", status=status)
            raise AuthError("Patreon returned HTML instead of JSON (not logged in?)", status=status)
        return
    if status in (403, 503) and looks_like_cloudflare(resp):
        raise CloudflareChallengeError("Cloudflare challenge page", status=status)
    detail = _error_detail(resp)
    if status == 401:
        raise AuthError("Patreon rejected the session (401)", status=status, detail=detail)
    if status == 403:
        if "json" in resp.content_type:
            raise ForbiddenError(detail or "forbidden", status=status, detail=detail)
        raise AuthError("Patreon rejected the request (403)", status=status, detail=detail)
    if status == 429:
        raise RateLimitedError(parse_retry_after(resp.headers.get("retry-after")), status=status)
    if status == 404:
        raise NotFoundError(detail or "not found", status=status, detail=detail)
    if status >= 500:
        raise TransientError(f"server error {status}", status=status, detail=detail)
    raise UnexpectedResponse(f"unexpected status {status}", status=status, detail=detail)


class PatreonClient:
    def __init__(
        self,
        transport: Transport,
        rate_limiter: RateLimiter,
        cookies: CookieSet,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._transport = transport
        self._limiter = rate_limiter
        self.cookies = cookies
        self.user_agent = user_agent or DEFAULT_USER_AGENT

    # ---- low level -----------------------------------------------------------------

    def headers(
        self,
        extra: dict[str, str] | None = None,
        *,
        api: bool = True,
        with_cookies: bool = True,
    ) -> dict[str, str]:
        h = {
            "User-Agent": self.user_agent,
            "Referer": f"{BASE_URL}/",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if api:
            h["Content-Type"] = "application/vnd.api+json"
            h["Accept"] = "application/json"
        cookie = self.cookies.header() if with_cookies else ""
        if cookie:
            h["Cookie"] = cookie
        if extra:
            h.update(extra)
        return h

    def media_headers(self) -> dict[str, str]:
        """Headers to hand to yt-dlp / direct downloads."""
        return {"User-Agent": self.user_agent, "Referer": f"{BASE_URL}/"}

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        stream: bool = False,
        expect_json: bool = True,
        api: bool = True,
        max_attempts: int = 3,
        classify: bool = True,
        with_cookies: bool = True,
    ) -> TransportResponse:
        attempt = 0
        while True:
            attempt += 1
            await self._limiter.wait()
            try:
                resp = await self._transport.request(
                    method,
                    url,
                    headers=self.headers(headers, api=api, with_cookies=with_cookies),
                    params=params,
                    stream=stream,
                )
            except TransportFailure as exc:
                if attempt >= max_attempts:
                    raise
                delay = 2.0 * attempt
                log.warning("%s %s failed (%s); retrying in %.0fs", method, url, exc, delay)
                await asyncio.sleep(delay)
                continue
            if not classify:
                return resp
            try:
                classify_response(resp, expect_json=expect_json and not stream)
            except RateLimitedError as exc:
                await resp.aclose()
                if attempt >= max_attempts:
                    raise
                delay = exc.retry_after * (1.5 ** (attempt - 1))
                log.warning("rate limited by Patreon; sleeping %.0fs", delay)
                await asyncio.sleep(delay)
                continue
            except TransientError as exc:
                await resp.aclose()
                if attempt >= max_attempts:
                    raise
                delay = 2.0 * attempt
                log.warning("Patreon %s; retrying in %.0fs", exc, delay)
                await asyncio.sleep(delay)
                continue
            except PatreonError:
                await resp.aclose()
                raise
            return resp

    async def api_get(
        self, path_or_url: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        url = (
            path_or_url
            if path_or_url.startswith("http")
            else f"{API_URL}/{path_or_url.lstrip('/')}"
        )
        if params is not None:
            params = {"json-api-version": "1.0", **params}
        resp = await self._request("GET", url, params=params)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise UnexpectedResponse("invalid JSON from Patreon") from exc
        if not isinstance(payload, dict):
            raise UnexpectedResponse("unexpected JSON shape from Patreon")
        return payload

    async def fetch_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        """GET a page or playlist as text. Also used for Mux HLS playlists (DRM probe),
        whose variants can point at any host, so cookies go to patreon.com hosts only."""
        resp = await self._request(
            "GET",
            url,
            headers=headers,
            expect_json=False,
            api=False,
            with_cookies=is_patreon_url(url),
        )
        return resp.text

    async def stream(
        self, url: str, *, range_start: int = 0, headers: dict[str, str] | None = None
    ) -> TransportResponse:
        """Open a streaming GET for a media URL. Status codes are NOT classified here.

        Session cookies are only sent to patreon.com hosts, never to CDNs.
        """
        extra = with_range(headers or {}, range_start)
        return await self._request(
            "GET",
            url,
            headers=extra,
            stream=True,
            expect_json=False,
            api=False,
            max_attempts=2,
            classify=False,
            with_cookies=is_patreon_url(url),
        )

    async def aclose(self) -> None:
        await self._transport.aclose()

    # ---- account -------------------------------------------------------------------

    async def get_current_user(self) -> UserInfo:
        if not self.cookies.is_configured:
            raise AuthError("no session_id configured")
        try:
            payload = await self.api_get(
                "current_user", {"fields[user]": "full_name,email,vanity,image_url"}
            )
        except ForbiddenError as exc:
            raise AuthError("session rejected", status=exc.status, detail=exc.detail) from exc
        data = payload.get("data") or {}
        attrs = data.get("attributes") or {}
        if not data.get("id"):
            raise AuthError("Patreon did not return a user for this session")
        return UserInfo(
            id=str(data["id"]),
            full_name=attrs.get("full_name"),
            email=attrs.get("email"),
            vanity=attrs.get("vanity"),
            image_url=attrs.get("image_url"),
        )

    async def get_pledges(self) -> list[SubscriptionInfo]:
        payload = await self.api_get(
            "current_user",
            {
                "include": "active_memberships.campaign",
                "fields[user]": "full_name",
                "fields[campaign]": PLEDGE_CAMPAIGN_FIELDS,
                "fields[member]": MEMBER_FIELDS,
                "json-api-use-default-includes": "false",
            },
        )
        index = IncludedIndex(payload)
        pledges: list[SubscriptionInfo] = []
        seen: set[str] = set()
        for item in payload.get("included") or []:
            if item.get("type") != "member":
                continue
            rels = item.get("relationships") or {}
            camp_ref = (rels.get("campaign") or {}).get("data")
            if not isinstance(camp_ref, dict):
                continue
            camp = index.get("campaign", camp_ref.get("id", ""))
            if camp is None or str(camp.get("id")) in seen:
                continue
            seen.add(str(camp["id"]))
            info = campaign_from_resource(camp)
            mattrs = item.get("attributes") or {}
            pledges.append(
                SubscriptionInfo(
                    external_id=info.external_id,
                    name=info.name,
                    handle=info.handle,
                    url=info.url,
                    avatar_url=info.avatar_url,
                    is_free=mattrs.get("is_free_member"),
                    is_trial=mattrs.get("is_free_trial"),
                    raw=item,
                )
            )
        # Fallback: some responses include campaigns without member rows.
        if not pledges:
            for item in payload.get("included") or []:
                if item.get("type") == "campaign" and str(item.get("id")) not in seen:
                    info = campaign_from_resource(item)
                    seen.add(info.external_id)
                    pledges.append(
                        SubscriptionInfo(
                            external_id=info.external_id,
                            name=info.name,
                            handle=info.handle,
                            url=info.url,
                            avatar_url=info.avatar_url,
                            raw=item,
                        )
                    )
        pledges.sort(key=lambda p: p.name.lower())
        return pledges

    # ---- campaigns -----------------------------------------------------------------

    @staticmethod
    def parse_creator_query(query: str) -> tuple[str | None, str | None, str | None]:
        """Return (campaign_id, vanity, user_id) parsed from a URL / vanity / id string."""
        q = query.strip()
        if not q:
            raise NotFoundError("empty creator query")
        if re.fullmatch(r"\d+", q):
            return q, None, None
        if q.lower().startswith("id:") and q[3:].isdigit():
            return q[3:], None, None
        if "://" not in q and not q.startswith("www.") and "patreon.com" not in q:
            q_url = f"{BASE_URL}/{q.lstrip('/')}"
        else:
            q_url = q if "://" in q else f"https://{q}"
        u = urlparse(q_url)
        if not u.netloc.endswith("patreon.com"):
            raise NotFoundError(f"not a patreon.com URL: {query}")
        qs = parse_qs(u.query)
        for key in ("c", "campaign_id"):
            if key in qs and qs[key][0].isdigit():
                return qs[key][0], None, None
        parts = [p for p in u.path.split("/") if p]
        if parts and parts[0] == "user" and "u" in qs:
            return None, None, qs["u"][0]
        if len(parts) >= 2 and parts[0] in ("c", "cw"):
            return None, parts[1], None
        if parts and parts[0].lower() not in RESERVED_PATHS:
            return None, parts[0], None
        raise NotFoundError(f"could not find a creator in '{query}'")

    async def _search_campaign_id(self, vanity: str) -> str | None:
        try:
            payload = await self.api_get("search", {"q": vanity, "page[size]": "5"})
        except (NotFoundError, UnexpectedResponse, ForbiddenError) as exc:
            log.debug("search for %s failed: %s", vanity, exc)
            return None
        for item in payload.get("data") or []:
            if item.get("type") != "campaign-document":
                continue
            attrs = item.get("attributes") or {}
            url = str(attrs.get("url") or "")
            if url.rstrip("/").lower().endswith(f"/{vanity.lower()}"):
                cid = str(item.get("id") or "")
                return cid.removeprefix("campaign_") or None
        return None

    async def resolve_campaign_id(self, query: str) -> str:
        campaign_id, vanity, user_id = self.parse_creator_query(query)
        if campaign_id:
            return campaign_id
        if vanity:
            cid = await self._search_campaign_id(vanity)
            if cid:
                return cid
            html = await self.fetch_text(f"{BASE_URL}/{vanity}")
            cid = extract_bootstrap_campaign_id(html)
            if cid:
                return cid
            raise NotFoundError(f"no campaign found for '{vanity}'")
        if user_id:
            html = await self.fetch_text(f"{BASE_URL}/user?u={user_id}")
            cid = extract_bootstrap_campaign_id(html)
            if cid:
                return cid
            raise NotFoundError(f"no campaign found for user {user_id}")
        raise NotFoundError(f"could not resolve '{query}'")

    async def get_campaign(self, campaign_id: str) -> CreatorInfo:
        payload = await self.api_get(
            f"campaigns/{campaign_id}",
            {
                "include": "creator",
                "fields[campaign]": CAMPAIGN_FIELDS,
                "fields[user]": USER_FIELDS,
                "json-api-use-default-includes": "false",
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise NotFoundError(f"campaign {campaign_id} not found")
        return campaign_from_resource(data, IncludedIndex(payload))

    # ---- posts ---------------------------------------------------------------------

    async def _get_posts_page(self, url: str, params: dict[str, str] | None) -> dict[str, Any]:
        try:
            return await self.api_get(url, params)
        except UnexpectedResponse as exc:
            if exc.status == 400 and params and "fields[post]" in params:
                # Defensive: if Patreon rejects one of our field names, ask for defaults.
                log.warning("Patreon rejected post fields (%s); retrying with defaults", exc.detail)
                slim = {k: v for k, v in params.items() if k != "fields[post]"}
                return await self.api_get(url, slim)
            raise

    async def iter_posts(
        self, campaign_id: str, *, sort: str = "-published_at"
    ) -> AsyncIterator[PostPage]:
        params = _post_query()
        params.update(
            {
                "filter[campaign_id]": campaign_id,
                "filter[contains_exclusive_posts]": "true",
                "filter[is_draft]": "false",
                "sort": sort,
            }
        )
        url: str | None = f"{API_URL}/posts"
        visited: set[str] = set()
        while url:
            if url in visited:  # a cursor that points back would otherwise loop forever
                log.warning("Patreon returned an already-visited page link; stopping: %s", url)
                return
            visited.add(url)
            payload = await self._get_posts_page(url, params)
            params = None
            index = IncludedIndex(payload)
            posts = [post_from_resource(p, index) for p in payload.get("data") or []]
            next_url = (payload.get("links") or {}).get("next")
            if isinstance(next_url, str) and next_url.startswith("/"):
                next_url = f"{BASE_URL}{next_url}"
            if not posts:
                next_url = None
            yield PostPage(posts=posts, next_url=next_url, raw=payload)
            url = next_url

    async def get_post(self, post_id: str) -> PostResource:
        payload = await self._get_posts_page(f"{API_URL}/posts/{post_id}", _post_query())
        data = payload.get("data")
        if not isinstance(data, dict):
            raise NotFoundError(f"post {post_id} not found")
        return post_from_resource(data, IncludedIndex(payload))

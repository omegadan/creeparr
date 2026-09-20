from __future__ import annotations

import httpx
import pytest

from patrearr.patreon.client import API_URL, BASE_URL, PatreonClient
from patrearr.patreon.errors import (
    AuthError,
    CloudflareChallengeError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    TransientError,
)
from tests import patreon_fixtures as fx
from tests.conftest import json_response


@pytest.mark.asyncio
async def test_get_current_user(client: PatreonClient, respx_mock):
    route = respx_mock.get(f"{API_URL}/current_user").mock(
        return_value=json_response(fx.current_user_response())
    )
    user = await client.get_current_user()
    assert user.id == "999" and user.full_name == "Test Patron"
    req = route.calls[0].request
    assert req.headers["cookie"] == "session_id=sid-123"
    assert req.headers["user-agent"].startswith("Patreon/")
    assert req.url.params["json-api-version"] == "1.0"


@pytest.mark.asyncio
async def test_no_session_is_auth_error(respx_mock):
    from tests.conftest import make_client

    c = make_client(session_id=None)
    with pytest.raises(AuthError):
        await c.get_current_user()
    await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,headers,exc",
    [
        (
            401,
            {"errors": [{"detail": "Unauthorized"}]},
            {"content-type": "application/json"},
            AuthError,
        ),
        (
            403,
            {"errors": [{"detail": "You do not have access"}]},
            {"content-type": "application/json"},
            ForbiddenError,
        ),
        (403, fx.CLOUDFLARE_HTML, {"content-type": "text/html"}, CloudflareChallengeError),
        (
            503,
            "<html>x</html>",
            {"content-type": "text/html", "cf-mitigated": "challenge"},
            CloudflareChallengeError,
        ),
        (200, "<html><body>login</body></html>", {"content-type": "text/html"}, AuthError),
        (
            404,
            {"errors": [{"detail": "missing"}]},
            {"content-type": "application/json"},
            NotFoundError,
        ),
    ],
)
async def test_error_classification(client, respx_mock, status, body, headers, exc):
    if isinstance(body, dict):
        resp = httpx.Response(status, json=body, headers=headers)
    else:
        resp = httpx.Response(status, text=body, headers=headers)
    respx_mock.get(f"{API_URL}/campaigns/1").mock(return_value=resp)
    with pytest.raises(exc):
        await client.get_campaign("1")


@pytest.mark.asyncio
async def test_rate_limit_then_success(client, respx_mock):
    route = respx_mock.get(f"{API_URL}/campaigns/1")
    route.side_effect = [
        httpx.Response(
            429, headers={"retry-after": "0", "content-type": "application/json"}, json={}
        ),
        json_response(fx.campaign_response()),
    ]
    info = await client.get_campaign("1")
    assert info.name == "Example Creator"
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_rate_limit_gives_up(client, respx_mock):
    respx_mock.get(f"{API_URL}/campaigns/1").mock(
        return_value=httpx.Response(
            429, headers={"retry-after": "0", "content-type": "application/json"}, json={}
        )
    )
    with pytest.raises(RateLimitedError):
        await client.get_campaign("1")


@pytest.mark.asyncio
async def test_transient_retry(client, respx_mock):
    route = respx_mock.get(f"{API_URL}/campaigns/1")
    route.side_effect = [
        httpx.Response(502, text="bad"),
        httpx.Response(503, text="bad"),
        json_response(fx.campaign_response()),
    ]
    assert (await client.get_campaign("1")).external_id == fx.CAMPAIGN_ID
    route.side_effect = [httpx.Response(500, text="x")] * 3
    with pytest.raises(TransientError):
        await client.get_campaign("1")


@pytest.mark.asyncio
async def test_iter_posts_follows_next(client, respx_mock):
    page2_url = f"{API_URL}/posts?page%5Bcursor%5D=abc&filter%5Bcampaign_id%5D={fx.CAMPAIGN_ID}"
    first = respx_mock.get(
        f"{API_URL}/posts",
        params__contains={"filter[campaign_id]": fx.CAMPAIGN_ID, "sort": "-published_at"},
    ).mock(
        return_value=json_response(
            fx.posts_page([fx.native_video_post("p1"), fx.youtube_post("p2")], next_url=page2_url)
        )
    )
    respx_mock.get(url=page2_url).mock(
        return_value=json_response(fx.posts_page([fx.vimeo_post("p3")]))
    )
    pages = [p async for p in client.iter_posts(fx.CAMPAIGN_ID)]
    assert [len(p.posts) for p in pages] == [2, 1]
    assert pages[0].next_url == page2_url and pages[1].next_url is None
    assert first.calls[0].request.url.params["include"].startswith("campaign,")


@pytest.mark.asyncio
async def test_iter_posts_falls_back_on_bad_fields(client, respx_mock):
    route = respx_mock.get(f"{API_URL}/posts")
    route.side_effect = [
        httpx.Response(
            400,
            json={"errors": [{"detail": "unknown field"}]},
            headers={"content-type": "application/json"},
        ),
        json_response(fx.posts_page([fx.native_video_post("p1")])),
    ]
    pages = [p async for p in client.iter_posts(fx.CAMPAIGN_ID)]
    assert len(pages[0].posts) == 1
    assert "fields[post]" not in route.calls[1].request.url.params


def test_parse_creator_query():
    p = PatreonClient.parse_creator_query
    assert p("123") == ("123", None, None)
    assert p("id:123") == ("123", None, None)
    assert p("examplecreator") == (None, "examplecreator", None)
    assert p("https://www.patreon.com/c/examplecreator/posts") == (None, "examplecreator", None)
    assert p("patreon.com/examplecreator") == (None, "examplecreator", None)
    assert p("https://www.patreon.com/user?u=55") == (None, None, "55")
    assert p("https://www.patreon.com/posts/some-post-1?c=999") == ("999", None, None)
    with pytest.raises(NotFoundError):
        p("https://example.com/foo")
    with pytest.raises(NotFoundError):
        p("https://www.patreon.com/login")


@pytest.mark.asyncio
async def test_resolve_campaign_id_via_search_then_bootstrap(client, respx_mock):
    respx_mock.get(f"{API_URL}/search").mock(return_value=json_response(fx.search_response()))
    assert await client.resolve_campaign_id("examplecreator") == fx.CAMPAIGN_ID
    # unknown vanity: search misses, page scrape hits
    respx_mock.get(f"{API_URL}/search").mock(return_value=json_response({"data": []}))
    respx_mock.get(f"{BASE_URL}/other").mock(
        return_value=httpx.Response(
            200, text=fx.CREATOR_PAGE_HTML, headers={"content-type": "text/html"}
        )
    )
    assert await client.resolve_campaign_id("other") == "1234567"
    respx_mock.get(f"{BASE_URL}/nobody").mock(
        return_value=httpx.Response(
            200, text="<html></html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(NotFoundError):
        await client.resolve_campaign_id("nobody")


@pytest.mark.asyncio
async def test_get_pledges(client, respx_mock):
    respx_mock.get(f"{API_URL}/current_user").mock(
        return_value=json_response(fx.pledges_response())
    )
    pledges = await client.get_pledges()
    assert [(p.external_id, p.is_free) for p in pledges] == [
        ("7654321", True),
        (fx.CAMPAIGN_ID, False),
    ]


@pytest.mark.asyncio
async def test_stream_does_not_send_cookies_to_cdn(client, respx_mock):
    route = respx_mock.get("https://c10.patreonusercontent.com/file.mp4").mock(
        return_value=httpx.Response(200, content=b"abc")
    )
    resp = await client.stream("https://c10.patreonusercontent.com/file.mp4", range_start=1)
    assert resp.status == 200
    req = route.calls[0].request
    assert "cookie" not in req.headers
    assert req.headers["range"] == "bytes=1-"
    assert req.headers["referer"] == "https://www.patreon.com/"
    await resp.aclose()

from __future__ import annotations

import json

import pytest

from patrearr.db.enums import MediaKind
from patrearr.patreon.transport import RateLimiter, TransportResponse
from patrearr.providers.onlyfans.client import OnlyFansClient, OnlyFansCredentials
from patrearr.providers.onlyfans.provider import OnlyFansProvider
from patrearr.providers.onlyfans.signing import DynamicRules
from tests import onlyfans_fixtures as of

RULES = DynamicRules("sp", "{}:{:x}", [0, 1], 0, "app-token", [])


class FakeTransport:
    """Routes OnlyFans API paths to fixture JSON based on URL and query params."""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[str] = []

    async def request(self, method, url, *, headers=None, params=None, stream=False):
        self.calls.append(url)
        for matcher, payload in self.routes:
            if matcher(url):
                return TransportResponse(
                    200, {"content-type": "application/json"}, content=json.dumps(payload).encode()
                )
        return TransportResponse(
            404, {"content-type": "application/json"}, content=b'{"error":{"code":404}}'
        )

    async def aclose(self):
        pass


def make_client(routes):
    return OnlyFansClient(
        FakeTransport(routes), RateLimiter(1000), OnlyFansCredentials("s", "1", "x", "UA"), RULES
    )


@pytest.mark.asyncio
async def test_iter_posts_paginates():
    routes = [
        (
            lambda u: "/posts" in u and "beforePublishTime" not in u,
            of.posts_page([1, 2, 3], has_more=True),
        ),
        (lambda u: "beforePublishTime" in u, of.posts_page([4], has_more=False)),
    ]
    client = make_client(routes)
    posts = [
        p for page in [pg async for pg in client.iter_posts(of.CREATOR_ID)] for p in page.posts
    ]
    assert [p.id for p in posts] == ["1", "2", "3", "4"]
    assert all(p.post_type == "onlyfans_post" for p in posts)
    assert posts[0].campaign_id == of.CREATOR_ID


@pytest.mark.asyncio
async def test_iter_messages_and_media():
    routes = [(lambda u: "/messages" in u, of.messages_page([50, 51], has_more=False))]
    client = make_client(routes)
    pages = [pg async for pg in client.iter_messages(of.CREATOR_ID)]
    msgs = pages[0].posts
    assert [m.id for m in msgs] == ["msg-50", "msg-51"]
    assert pages[0].source == "messages"
    assert msgs[0].media[0].metadata["of_type"] == "video"


@pytest.mark.asyncio
async def test_archived_source_label():
    routes = [(lambda u: "/posts/archived" in u, of.posts_page([9], has_more=False))]
    client = make_client(routes)
    pages = [pg async for pg in client.iter_archived(of.CREATOR_ID)]
    assert pages[0].source == "archived" and pages[0].posts[0].id == "9"


def test_drm_media_resolves_flagged():
    p = of.post(1, media=[of.VIDEO_DRM])
    pr = OnlyFansClient._post_from_json(p)
    prov = object.__new__(OnlyFansProvider)
    specs = OnlyFansProvider.resolve_media(prov, pr)
    assert len(specs) == 1 and specs[0].kind == MediaKind.VIDEO
    assert specs[0].metadata["drm"] is True


@pytest.mark.asyncio
async def test_get_me_and_resolve_creator():
    routes = [
        (lambda u: u.endswith("/users/me"), of.user()),
        (lambda u: "/users/examplemodel" in u, of.creator_profile()),
    ]
    client = make_client(routes)
    me = await client.get_me()
    assert me.id == "393746487" and me.full_name == "Test Patron"
    info = await client.resolve_creator("https://onlyfans.com/examplemodel")
    assert info.external_id == of.CREATOR_ID and info.handle == "examplemodel"

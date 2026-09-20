from __future__ import annotations

from patrearr.patreon.parsing import (
    IncludedIndex,
    campaign_from_resource,
    extract_bootstrap_campaign_id,
    post_from_resource,
)
from tests import patreon_fixtures as fx


def test_post_from_resource_resolves_media_relationships():
    page = fx.posts_page(
        [fx.image_post("p1", ["m1", "m2"], image_order=["m2", "m1"])],
        included=[
            fx.media_resource("m1"),
            fx.media_resource("m2", file_name="two.png", mimetype="image/png"),
        ],
    )
    index = IncludedIndex(page)
    post = post_from_resource(page["data"][0], index)
    assert post.id == "p1"
    assert post.campaign_id == fx.CAMPAIGN_ID
    assert post.published_at is not None and post.published_at.year == 2026
    assert {m.id for m in post.media} == {"m1", "m2"}
    assert all(m.relationship == "images" for m in post.media)
    assert post.thumbnail_url.endswith("thumb_large.jpg")
    stored = post.storable_json()
    assert stored["data"]["id"] == "p1" and len(stored["included"]) == 2


def test_unresolved_relationship_keeps_stub():
    page = fx.posts_page([fx.image_post("p1", ["missing"])], included=[])
    post = post_from_resource(page["data"][0], IncludedIndex(page))
    assert post.media[0].id == "missing"
    assert post.media[0].best_url is None


def test_campaign_from_resource_with_creator():
    payload = fx.campaign_response()
    info = campaign_from_resource(payload["data"], IncludedIndex(payload))
    assert info.campaign_id == fx.CAMPAIGN_ID
    assert info.vanity == fx.VANITY
    assert info.creator_user_id == "42"
    assert info.creator_name == "Example Person"
    assert info.avatar_url.endswith("avatar.jpg")


def test_bootstrap_patterns():
    assert extract_bootstrap_campaign_id(fx.CREATOR_PAGE_HTML) == "1234567"
    nextjs = 'x{"value":{"campaign":{"data":{"id":"555","type":"campaign"}}}}'
    assert extract_bootstrap_campaign_id(nextjs) == "555"
    escaped = 'self.__next_f.push([1,"{\\"campaign\\":{\\"data\\":{\\"id\\":\\"777\\"}}}"])'
    assert extract_bootstrap_campaign_id(escaped) == "777"
    assert extract_bootstrap_campaign_id("<html>nothing</html>") is None

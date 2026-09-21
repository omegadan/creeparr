from __future__ import annotations

from datetime import UTC, datetime

from creeparr.db.enums import MediaKind
from creeparr.providers.instagram.client import (
    creator_from_items,
    group_into_posts,
)
from creeparr.providers.instagram.provider import InstagramProvider


def _items():
    date = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    common = {"username": "someone", "fullname": "Some One", "owner_id": "555", "date": date}
    return [
        (
            "https://cdn/1.jpg",
            {
                **common,
                "post_shortcode": "AAA",
                "num": 1,
                "count": 2,
                "typename": "GraphSidecar",
                "description": "hi",
                "extension": "jpg",
            },
        ),
        (
            "https://cdn/2.mp4",
            {
                **common,
                "post_shortcode": "AAA",
                "num": 2,
                "count": 2,
                "typename": "GraphVideo",
                "video_url": "https://cdn/2.mp4",
                "extension": "mp4",
            },
        ),
        (
            "https://cdn/3.jpg",
            {
                **common,
                "post_shortcode": "BBB",
                "num": 1,
                "count": 1,
                "typename": "GraphImage",
                "description": "second",
                "extension": "jpg",
            },
        ),
    ]


def test_group_into_posts_and_resolve():
    posts = group_into_posts(_items(), "posts", "someone")
    assert [p.id for p in posts] == ["AAA", "BBB"]
    aaa = posts[0]
    assert aaa.post_type == "instagram_post" and aaa.campaign_id == "555"
    assert aaa.published_at is not None and aaa.published_at.year == 2026
    assert len(aaa.media) == 2 and aaa.title == "hi"

    prov = object.__new__(InstagramProvider)
    specs = InstagramProvider.resolve_media(prov, aaa)
    assert [s.media_key for s in specs] == ["ig:AAA:1", "ig:AAA:2"]
    kinds = {s.media_key: s.kind for s in specs}
    assert kinds["ig:AAA:1"] == MediaKind.IMAGE and kinds["ig:AAA:2"] == MediaKind.VIDEO


def test_reel_source_post_type():
    posts = group_into_posts(_items()[:1], "reels", "someone")
    assert posts[0].post_type == "instagram_reel"


def test_creator_from_items():
    info = creator_from_items(_items(), "someone")
    assert info.external_id == "someone" and info.handle == "someone"
    assert info.name == "Some One" and info.owner_user_id == "555"


def test_username_parsing():
    u = InstagramProvider._username
    assert u("https://www.instagram.com/someone/") == "someone"
    assert u("https://www.instagram.com/stories/someone/") == "someone"
    assert u("@someone") == "someone"
    assert u("someone") == "someone"


def test_enabled_sources_respects_toggles(env, settings, session_factory, bus):
    from creeparr.core.events import EventBus  # noqa: F401

    prov = InstagramProvider(env, settings, session_factory, bus)
    settings.update(
        {
            "instagram": {
                "include_reels": True,
                "include_stories": True,
                "include_highlights": False,
                "include_tagged": True,
            }
        }
    )
    assert prov._enabled_sources() == ["posts", "reels", "stories", "tagged"]

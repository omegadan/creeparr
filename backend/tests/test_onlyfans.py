from __future__ import annotations

from patrearr.db.enums import MediaKind
from patrearr.providers.onlyfans.client import OnlyFansClient
from patrearr.providers.onlyfans.signing import DynamicRules, sign_request

RULES = DynamicRules(
    static_param="abc123",
    format="{}:{:x}:0a:something",
    checksum_indexes=[0, 5, 10, 15],
    checksum_constant=42,
    app_token="tok",
    remove_headers=[],
)


def test_sign_request_is_deterministic_and_uses_path():
    a = sign_request("https://onlyfans.com/api2/v2/users/me", RULES, "999", now=1700000000000)
    b = sign_request("https://onlyfans.com/api2/v2/users/me", RULES, "999", now=1700000000000)
    assert a == b
    assert a["time"] == "1700000000000"
    parts = a["sign"].split(":")
    assert len(parts[0]) == 40  # sha1 hex
    # a different path yields a different signature
    c = sign_request("https://onlyfans.com/api2/v2/users/12/posts", RULES, "999", now=1700000000000)
    assert c["sign"] != a["sign"]


def test_post_parsing_and_media_resolution():
    raw = {
        "id": 555,
        "text": "Hello <b>world</b> " + "x" * 400,
        "postedAt": "2026-03-01T12:00:00+00:00",
        "canViewMedia": True,
        "author": {"id": 12, "username": "someone"},
        "media": [
            {
                "id": 1,
                "type": "photo",
                "canView": True,
                "files": {"full": {"url": "https://cdn.onlyfans.com/1.jpg"}},
            },
            {
                "id": 2,
                "type": "video",
                "canView": True,
                "files": {"full": {"url": "https://cdn.onlyfans.com/2.mp4"}},
            },
            {
                "id": 3,
                "type": "video",
                "canView": True,
                "files": {"full": {"url": "https://cdn.onlyfans.com/3.mp4", "drm": True}},
            },
            {"id": 4, "type": "video", "canView": False, "files": {}},
        ],
    }
    post = OnlyFansClient._post_from_json(raw)
    assert post.id == "555"
    assert post.campaign_id == "12"
    assert len(post.title) <= 200
    assert len(post.media) == 4

    from patrearr.providers.onlyfans.provider import OnlyFansProvider

    prov = OnlyFansProvider.__new__(OnlyFansProvider)  # resolve_media needs no I/O
    specs = OnlyFansProvider.resolve_media(prov, post)
    # photo + two videos are viewable (drm kept but flagged); the canView=false one is dropped
    assert [s.media_key for s in specs] == ["media:1", "media:2", "media:3"]
    kinds = {s.media_key: s.kind for s in specs}
    assert kinds["media:1"] == MediaKind.IMAGE and kinds["media:2"] == MediaKind.VIDEO
    assert next(s for s in specs if s.media_key == "media:3").metadata["drm"] is True


def test_no_access_post_yields_no_media():
    post = OnlyFansClient._post_from_json({"id": 1, "canViewMedia": False, "media": []})
    prov = object.__new__(
        __import__(
            "patrearr.providers.onlyfans.provider", fromlist=["OnlyFansProvider"]
        ).OnlyFansProvider
    )
    assert prov.resolve_media(post) == []


def test_message_parsing_and_kind():
    msg = {
        "id": 900,
        "text": "here you go 💋",
        "createdAt": "2026-02-01T10:00:00+00:00",
        "canPurchase": False,
        "media": [
            {
                "id": 5,
                "type": "video",
                "canView": True,
                "files": {"full": {"url": "https://cdn.onlyfans.com/5.mp4"}},
            }
        ],
    }
    pr = OnlyFansClient._message_from_json(msg, "123")
    assert pr.id == "msg-900" and pr.post_type == "onlyfans_message"
    assert pr.campaign_id == "123" and pr.current_user_can_view is True
    assert pr.raw["_kind"] == "message"
    from patrearr.providers.onlyfans.client import rebuild_post

    rebuilt = rebuild_post({"data": pr.raw})
    assert rebuilt is not None and rebuilt.id == "msg-900"


def test_locked_ppv_message_not_viewable():
    msg = {
        "id": 1,
        "text": "unlock me",
        "canPurchase": True,
        "createdAt": "2026-01-01T00:00:00+00:00",
        "media": [{"id": 2, "type": "video", "canView": False, "files": {}}],
    }
    pr = OnlyFansClient._message_from_json(msg, "9")
    assert pr.current_user_can_view is False


def test_youtube_channel_url_and_entry_parsing():
    from patrearr.providers.youtube import YouTubeProvider, _parse_rss

    u = YouTubeProvider._channel_videos_url
    assert u("https://www.youtube.com/@TED").endswith("/@TED/videos")
    assert u("https://www.youtube.com/@TED/streams").endswith("/@TED/videos")
    assert u("UCAuUUnT6oDeKwE6v1NGQxug").endswith("/channel/UCAuUUnT6oDeKwE6v1NGQxug/videos")
    assert u("@TED").endswith("/@TED/videos")

    prov = object.__new__(YouTubeProvider)
    pr = YouTubeProvider._post_from_entry(
        prov,
        {
            "id": "abc123",
            "title": "Hi",
            "url": "https://www.youtube.com/watch?v=abc123",
            "timestamp": 1735689600,
        },
        "UC123",
    )
    assert pr.id == "abc123" and pr.post_type == "youtube_video" and pr.campaign_id == "UC123"
    assert pr.published_at is not None
    specs = YouTubeProvider.resolve_media(prov, pr)
    assert (
        len(specs) == 1
        and specs[0].source == "embed_youtube"
        and specs[0].media_key == "video:abc123"
    )

    rss = _parse_rss(
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>xyz</yt:videoId><published>2026-01-02T03:04:05+00:00</published></entry></feed>'
    )
    assert "xyz" in rss and rss["xyz"].year == 2026

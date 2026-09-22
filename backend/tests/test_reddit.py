from __future__ import annotations

from creeparr.db.enums import MediaKind, MediaSource
from creeparr.providers.reddit.client import _post_from_json
from creeparr.providers.reddit.provider import RedditProvider


def test_image_post():
    p = _post_from_json(
        {
            "id": "abc",
            "title": "cat",
            "created_utc": 1735689600,
            "url": "https://i.redd.it/x.jpg",
            "post_hint": "image",
        },
        "r/aww",
    )
    assert p is not None and p.post_type == "reddit_post" and p.campaign_id == "r/aww"
    assert p.published_at is not None and len(p.media) == 1
    assert p.media[0].metadata["is_video"] is False


def test_gallery_post():
    p = _post_from_json(
        {
            "id": "g1",
            "title": "gallery",
            "created_utc": 1,
            "is_gallery": True,
            "gallery_data": {"items": [{"media_id": "m1"}, {"media_id": "m2"}]},
            "media_metadata": {
                "m1": {"e": "Image", "s": {"u": "https://cdn/m1.jpg"}},
                "m2": {"e": "Image", "s": {"u": "https://cdn/m2.png"}},
            },
        },
        "r/pics",
    )
    assert p is not None and len(p.media) == 2
    assert [m.id for m in p.media] == ["g1:1", "g1:2"]


def test_reddit_video_routes_to_permalink():
    p = _post_from_json(
        {
            "id": "v1",
            "title": "clip",
            "created_utc": 1,
            "is_video": True,
            "media": {"reddit_video": {"fallback_url": "https://v.redd.it/v1/DASH_720.mp4"}},
            "permalink": "/r/x/comments/v1/clip/",
        },
        "r/videos",
    )
    assert p is not None and len(p.media) == 1
    m = p.media[0]
    assert m.metadata["is_video"] is True
    assert m.download_url == "https://www.reddit.com/r/x/comments/v1/clip/"  # yt-dlp merges audio

    prov = object.__new__(RedditProvider)
    specs = RedditProvider.resolve_media(prov, p)
    assert specs[0].source == MediaSource.EMBED_OTHER and specs[0].kind == MediaKind.VIDEO


def test_self_post_has_no_media():
    assert (
        _post_from_json(
            {"id": "s1", "title": "text", "is_self": True, "created_utc": 1, "selftext": "hi"},
            "r/x",
        )
        is None
    )


def test_gifv_becomes_mp4():
    p = _post_from_json(
        {"id": "gv", "title": "g", "created_utc": 1, "url": "https://i.imgur.com/x.gifv"}, "r/gifs"
    )
    assert p.media[0].download_url.endswith(".mp4") and p.media[0].metadata["is_video"] is True


def test_target_parsing():
    t = RedditProvider._target
    assert t("https://www.reddit.com/r/aww/") == "r/aww"
    assert t("https://www.reddit.com/user/spez/") == "u/spez"
    assert t("https://old.reddit.com/u/spez") == "u/spez"
    assert t("r/pics") == "r/pics"
    assert t("u/spez") == "u/spez"
    assert t("aww") == "r/aww"


def test_image_resolve_media_direct():
    p = _post_from_json(
        {
            "id": "i",
            "title": "t",
            "created_utc": 1,
            "url": "https://i.redd.it/x.png",
            "post_hint": "image",
        },
        "r/a",
    )
    prov = object.__new__(RedditProvider)
    specs = RedditProvider.resolve_media(prov, p)
    assert specs[0].source == MediaSource.MEDIA_DOWNLOAD and specs[0].kind == MediaKind.IMAGE


def test_post_from_raw_round_trips_media_keys():
    # reresolve_media rebuilds posts from stored raw_json; the media keys (and file names,
    # which derive from them) must match a scan, or the rows get deleted and re-downloaded.
    prov = object.__new__(RedditProvider)
    for raw in (
        {
            "id": "g1",
            "title": "gallery",
            "created_utc": 1,
            "is_gallery": True,
            "gallery_data": {"items": [{"media_id": "m1"}, {"media_id": "m2"}]},
            "media_metadata": {
                "m1": {"e": "Image", "s": {"u": "https://cdn/m1.jpg"}},
                "m2": {"e": "Image", "s": {"u": "https://cdn/m2.png"}},
            },
        },
        {
            "id": "v1",
            "title": "clip",
            "created_utc": 1,
            "is_video": True,
            "media": {"reddit_video": {"fallback_url": "https://v.redd.it/x/DASH_720.mp4"}},
            "permalink": "/r/a/comments/v1/clip/",
        },
    ):
        scanned = _post_from_json(raw, "r/a")
        rebuilt = RedditProvider.post_from_raw(prov, scanned.storable_json())
        assert rebuilt is not None

        def view(post):
            return [
                (s.media_key, s.kind, s.source, s.file_name)
                for s in RedditProvider.resolve_media(prov, post)
            ]

        assert view(rebuilt) == view(scanned)
        assert view(scanned)  # non-trivial

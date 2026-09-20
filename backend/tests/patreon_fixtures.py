"""Builders for Patreon JSON:API payloads used across tests.

Shapes mirror what www.patreon.com/api returns (as documented by gallery-dl / patreon-dl);
values are synthetic.
"""

from __future__ import annotations

from typing import Any

CAMPAIGN_ID = "1234567"
VANITY = "examplecreator"
MUX_URL = "https://stream.mux.com/abc123DEF456.m3u8?token=signed"
CDN = "https://c10.patreonusercontent.com/4/patreon-media/p/post"


def campaign_resource(
    campaign_id: str = CAMPAIGN_ID, name: str = "Example Creator"
) -> dict[str, Any]:
    return {
        "type": "campaign",
        "id": campaign_id,
        "attributes": {
            "name": name,
            "vanity": VANITY,
            "url": f"https://www.patreon.com/{VANITY}",
            "avatar_photo_url": "https://c10.patreonusercontent.com/avatar.jpg",
            "avatar_photo_image_urls": {"default": "https://c10.patreonusercontent.com/avatar.jpg"},
            "cover_photo_url": "https://c10.patreonusercontent.com/cover.jpg",
            "creation_name": "videos about things",
            "is_nsfw": False,
        },
        "relationships": {"creator": {"data": {"type": "user", "id": "42"}}},
    }


def campaign_response(campaign_id: str = CAMPAIGN_ID) -> dict[str, Any]:
    return {
        "data": campaign_resource(campaign_id),
        "included": [
            {
                "type": "user",
                "id": "42",
                "attributes": {
                    "full_name": "Example Person",
                    "url": "https://www.patreon.com/examplecreator",
                },
            }
        ],
    }


def current_user_response() -> dict[str, Any]:
    return {
        "data": {
            "type": "user",
            "id": "999",
            "attributes": {
                "full_name": "Test Patron",
                "email": "patron@example.com",
                "vanity": "testpatron",
            },
        }
    }


def pledges_response() -> dict[str, Any]:
    return {
        "data": {"type": "user", "id": "999", "attributes": {"full_name": "Test Patron"}},
        "included": [
            {
                "type": "member",
                "id": "m1",
                "attributes": {"is_free_member": False, "is_free_trial": False},
                "relationships": {"campaign": {"data": {"type": "campaign", "id": CAMPAIGN_ID}}},
            },
            {
                "type": "member",
                "id": "m2",
                "attributes": {"is_free_member": True, "is_free_trial": False},
                "relationships": {"campaign": {"data": {"type": "campaign", "id": "7654321"}}},
            },
            campaign_resource(CAMPAIGN_ID),
            campaign_resource("7654321", "Another Creator"),
        ],
    }


def search_response(vanity: str = VANITY, campaign_id: str = CAMPAIGN_ID) -> dict[str, Any]:
    return {
        "data": [
            {
                "type": "campaign-document",
                "id": f"campaign_{campaign_id}",
                "attributes": {
                    "url": f"https://www.patreon.com/{vanity}",
                    "name": "Example Creator",
                },
            },
            {
                "type": "campaign-document",
                "id": "campaign_1111",
                "attributes": {"url": "https://www.patreon.com/somethingelse", "name": "Other"},
            },
        ]
    }


def media_resource(
    media_id: str,
    *,
    file_name: str = "image.jpg",
    mimetype: str | None = "image/jpeg",
    download_url: str | None = None,
    size: int | None = 1000,
) -> dict[str, Any]:
    return {
        "type": "media",
        "id": media_id,
        "attributes": {
            "file_name": file_name,
            "mimetype": mimetype,
            "download_url": download_url or f"{CDN}/{media_id}/{file_name}?token=x",
            "image_urls": {"original": f"{CDN}/{media_id}/{file_name}?token=x"},
            "size_bytes": size,
            "metadata": {},
        },
    }


def post_resource(
    post_id: str,
    *,
    title: str = "A post",
    post_type: str = "video_external_file",
    published_at: str = "2026-03-14T15:09:00.000+00:00",
    edited_at: str | None = None,
    can_view: bool = True,
    post_file: dict[str, Any] | None = None,
    embed: dict[str, Any] | None = None,
    images: list[str] | None = None,
    audio: list[str] | None = None,
    attachments: list[str] | None = None,
    image_order: list[str] | None = None,
    content: str = "<p>Hello <b>world</b></p>",
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "title": title,
        "content": content if can_view else None,
        "teaser_text": "teaser",
        "published_at": published_at,
        "edited_at": edited_at,
        "url": f"https://www.patreon.com/posts/{post_id}",
        "patreon_url": f"/posts/{post_id}",
        "post_type": post_type,
        "current_user_can_view": can_view,
        "embed": embed if can_view else None,
        "post_file": post_file if can_view else None,
        "image": {
            "url": f"{CDN}/{post_id}/thumb.jpg",
            "large_url": f"{CDN}/{post_id}/thumb_large.jpg",
        },
        "post_metadata": {"image_order": image_order} if image_order else None,
    }
    rels: dict[str, Any] = {"campaign": {"data": {"type": "campaign", "id": CAMPAIGN_ID}}}

    def rel(ids: list[str] | None) -> dict[str, Any]:
        return {"data": [{"type": "media", "id": i} for i in (ids or [])]}

    rels["images"] = rel(images if can_view else None)
    rels["audio"] = rel(audio if can_view else None)
    rels["attachments_media"] = rel(attachments if can_view else None)
    rels["media"] = rel(
        ((images or []) + (audio or []) + (attachments or [])) if can_view else None
    )
    return {"type": "post", "id": post_id, "attributes": attrs, "relationships": rels}


def native_video_post(post_id: str, *, hls: bool = False, **kw: Any) -> dict[str, Any]:
    pf = {
        "name": "episode.mp4",
        "url": MUX_URL if hls else f"{CDN}/{post_id}/episode.mp4?token=x",
        "mimetype": "video/mp4",
    }
    return post_resource(post_id, post_type="video_external_file", post_file=pf, **kw)


def youtube_post(post_id: str, **kw: Any) -> dict[str, Any]:
    return post_resource(
        post_id,
        post_type="video_embed",
        embed={
            "provider": "YouTube",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "provider_url": "https://www.youtube.com",
        },
        **kw,
    )


def vimeo_post(post_id: str, **kw: Any) -> dict[str, Any]:
    return post_resource(
        post_id,
        post_type="video_embed",
        embed={
            "provider": "Vimeo",
            "url": "https://vimeo.com/123456789/abcdef1234",
            "provider_url": "https://vimeo.com",
        },
        **kw,
    )


def image_post(post_id: str, media_ids: list[str], **kw: Any) -> dict[str, Any]:
    return post_resource(
        post_id,
        post_type="image_file",
        post_file={"name": "1.jpg", "url": f"{CDN}/{media_ids[0]}/1.jpg?token=x"},
        images=media_ids,
        **kw,
    )


def post_response(
    post: dict[str, Any], included: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"data": post, "included": [campaign_resource(), *(included or [])]}


def posts_page(
    posts: list[dict[str, Any]],
    included: list[dict[str, Any]] | None = None,
    next_url: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "data": posts,
        "included": [campaign_resource(), *(included or [])],
        "meta": {"pagination": {"total": len(posts)}},
    }
    if next_url:
        payload["links"] = {"next": next_url}
    return payload


CREATOR_PAGE_HTML = """<!doctype html><html><head><title>Example</title></head><body>
<script>
Object.assign(window.patreon, {"bootstrap": {"campaign": {"data": {"id": "1234567", "type": "campaign"}}}});
</script></body></html>"""

CLOUDFLARE_HTML = """<!DOCTYPE html><html><head><title>Just a moment...</title></head>
<body><div id="cf-chl-widget">Checking your browser</div><script src="/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1"></script></body></html>"""

MUX_MASTER_CLEAR = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2"
rendition_720.m3u8?token=x
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
rendition_360.m3u8?token=x
"""

MUX_VARIANT_CLEAR = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:5
#EXTINF:5.0,
seg0.ts
#EXTINF:5.0,
seg1.ts
#EXT-X-ENDLIST
"""

MUX_VARIANT_AES128 = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="https://example.com/key",IV=0x1234
#EXTINF:5.0,
seg0.ts
#EXT-X-ENDLIST
"""

MUX_MASTER_DRM = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="skd://12345",KEYFORMAT="com.apple.streamingkeydelivery",KEYFORMATVERSIONS="1"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
rendition_720.m3u8
"""

MUX_VARIANT_WIDEVINE = """#EXTM3U
#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,URI="data:text/plain;base64,AAAA",KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",KEYFORMATVERSIONS="1"
#EXTINF:4.0,
seg0.m4s
#EXT-X-ENDLIST
"""

"""JSON:API parsing helpers and creator-page bootstrap scraping."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from patrearr.patreon.models import CampaignInfo, MediaResource, PostResource

MEDIA_RELATIONSHIPS = ("images", "audio", "attachments_media", "media")


def parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    v = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


class IncludedIndex:
    """Index of the `included` array of a JSON:API response keyed by (type, id)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        for item in payload.get("included") or []:
            if isinstance(item, dict) and "type" in item and "id" in item:
                self._items[(str(item["type"]), str(item["id"]))] = item

    def get(self, type_: str, id_: str) -> dict[str, Any] | None:
        return self._items.get((str(type_), str(id_)))

    def resolve(self, rel: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Resolve a relationship object to a list of included resources (in order)."""
        if not rel:
            return []
        data = rel.get("data")
        if data is None:
            return []
        if isinstance(data, dict):
            data = [data]
        out = []
        for ref in data:
            if not isinstance(ref, dict):
                continue
            item = self.get(ref.get("type", ""), ref.get("id", ""))
            if item is None:
                # Unresolved reference: keep a stub so ids are still known.
                item = {"type": ref.get("type"), "id": ref.get("id"), "attributes": {}}
            out.append(item)
        return out


def _to_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def media_from_resource(item: dict[str, Any], relationship: str) -> MediaResource:
    attrs = item.get("attributes") or {}
    return MediaResource(
        id=str(item.get("id")),
        relationship=relationship,
        file_name=attrs.get("file_name"),
        download_url=attrs.get("download_url"),
        image_urls=attrs.get("image_urls") or {},
        mimetype=attrs.get("mimetype"),
        size_bytes=_to_int(attrs.get("size_bytes")),
        metadata=attrs.get("metadata") or {},
        raw=item,
    )


def post_from_resource(item: dict[str, Any], index: IncludedIndex) -> PostResource:
    attrs = item.get("attributes") or {}
    rels = item.get("relationships") or {}

    media: list[MediaResource] = []
    seen: set[str] = set()
    for rel_name in MEDIA_RELATIONSHIPS:
        for res in index.resolve(rels.get(rel_name)):
            mid = str(res.get("id"))
            if mid in seen:
                # keep the first relationship we saw it under; but remember it's also generic media
                continue
            seen.add(mid)
            media.append(media_from_resource(res, rel_name))

    campaign_id = None
    camp = rels.get("campaign", {}).get("data") if isinstance(rels.get("campaign"), dict) else None
    if isinstance(camp, dict):
        campaign_id = str(camp.get("id"))

    return PostResource(
        id=str(item.get("id")),
        title=attrs.get("title") or "",
        post_type=attrs.get("post_type"),
        content=attrs.get("content"),
        teaser_text=attrs.get("teaser_text"),
        url=attrs.get("url") or attrs.get("patreon_url"),
        published_at=parse_datetime(attrs.get("published_at")),
        edited_at=parse_datetime(attrs.get("edited_at")),
        current_user_can_view=bool(attrs.get("current_user_can_view", True)),
        embed=attrs.get("embed") if isinstance(attrs.get("embed"), dict) else None,
        post_file=attrs.get("post_file") if isinstance(attrs.get("post_file"), dict) else None,
        image=attrs.get("image") if isinstance(attrs.get("image"), dict) else None,
        thumbnail=attrs.get("thumbnail") if isinstance(attrs.get("thumbnail"), dict) else None,
        post_metadata=attrs.get("post_metadata")
        if isinstance(attrs.get("post_metadata"), dict)
        else None,
        campaign_id=campaign_id,
        media=media,
        raw=item,
    )


def campaign_from_resource(
    item: dict[str, Any], index: IncludedIndex | None = None
) -> CampaignInfo:
    attrs = item.get("attributes") or {}
    rels = item.get("relationships") or {}
    creator_id = None
    creator_name = None
    creator_rel = rels.get("creator") if isinstance(rels.get("creator"), dict) else None
    if creator_rel and isinstance(creator_rel.get("data"), dict):
        creator_id = str(creator_rel["data"].get("id"))
        if index is not None:
            user = index.get("user", creator_id)
            if user:
                creator_name = (user.get("attributes") or {}).get("full_name")
    avatar = attrs.get("avatar_photo_url")
    if not avatar and isinstance(attrs.get("avatar_photo_image_urls"), dict):
        urls = attrs["avatar_photo_image_urls"]
        avatar = urls.get("default") or urls.get("original") or next(iter(urls.values()), None)
    return CampaignInfo(
        campaign_id=str(item.get("id")),
        name=attrs.get("name") or attrs.get("creation_name") or f"campaign {item.get('id')}",
        vanity=attrs.get("vanity"),
        url=attrs.get("url"),
        avatar_url=avatar,
        cover_url=attrs.get("cover_photo_url"),
        creation_name=attrs.get("creation_name"),
        is_nsfw=attrs.get("is_nsfw"),
        creator_user_id=creator_id,
        creator_name=creator_name,
        raw=item,
    )


_BOOTSTRAP_PATTERNS = (
    re.compile(r'"campaign"\s*:\s*\{\s*"data"\s*:\s*\{\s*"id"\s*:\s*"(\d+)"'),
    re.compile(r'\\"campaign\\"\s*:\s*\{\s*\\"data\\"\s*:\s*\{\s*\\"id\\"\s*:\s*\\"(\d+)\\"'),
    re.compile(r'"campaign_id"\s*:\s*"?(\d+)"?'),
    re.compile(r'"campaignId"\s*:\s*"?(\d+)"?'),
)


def extract_bootstrap_campaign_id(html: str) -> str | None:
    """Find the campaign id embedded in a creator page's bootstrap / Next.js data."""
    for pat in _BOOTSTRAP_PATTERNS:
        m = pat.search(html)
        if m:
            return m.group(1)
    return None

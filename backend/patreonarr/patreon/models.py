"""Plain data structures returned by the Patreon client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from patreonarr.db.enums import MediaKind, MediaSource


@dataclass
class UserInfo:
    id: str
    full_name: str | None = None
    email: str | None = None
    vanity: str | None = None
    image_url: str | None = None


@dataclass
class PledgeInfo:
    campaign_id: str
    name: str
    vanity: str | None = None
    url: str | None = None
    avatar_url: str | None = None
    is_free_member: bool | None = None
    is_free_trial: bool | None = None


@dataclass
class CampaignInfo:
    campaign_id: str
    name: str
    vanity: str | None = None
    url: str | None = None
    avatar_url: str | None = None
    cover_url: str | None = None
    creation_name: str | None = None
    is_nsfw: bool | None = None
    creator_user_id: str | None = None
    creator_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaResource:
    id: str
    relationship: str  # images | audio | media | attachments_media
    file_name: str | None = None
    download_url: str | None = None
    image_urls: dict[str, Any] = field(default_factory=dict)
    mimetype: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def best_url(self) -> str | None:
        if self.download_url:
            return self.download_url
        for key in ("original", "default", "url"):
            v = self.image_urls.get(key)
            if isinstance(v, str) and v:
                return v
        return None


@dataclass
class PostResource:
    id: str
    title: str
    post_type: str | None
    content: str | None = None
    teaser_text: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    edited_at: datetime | None = None
    current_user_can_view: bool = True
    embed: dict[str, Any] | None = None
    post_file: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    thumbnail: dict[str, Any] | None = None
    post_metadata: dict[str, Any] | None = None
    campaign_id: str | None = None
    media: list[MediaResource] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def thumbnail_url(self) -> str | None:
        for src in (self.image, self.thumbnail):
            if isinstance(src, dict):
                for key in ("large_url", "url", "thumb_url"):
                    v = src.get(key)
                    if isinstance(v, str) and v:
                        return v
        return None

    @property
    def embed_provider(self) -> str | None:
        if isinstance(self.embed, dict):
            v = self.embed.get("provider")
            return str(v) if v else None
        return None

    @property
    def embed_url(self) -> str | None:
        if isinstance(self.embed, dict):
            v = self.embed.get("url")
            return str(v) if v else None
        return None

    def storable_json(self) -> dict[str, Any]:
        """What we persist as raw_json / post.json: the resource plus its media resources."""
        return {"data": self.raw, "included": [m.raw for m in self.media]}


@dataclass
class MediaSpec:
    media_key: str
    kind: MediaKind
    source: MediaSource
    url: str
    file_name: str | None = None
    mimetype: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    order_index: int = 0


@dataclass
class PostPage:
    posts: list[PostResource]
    next_url: str | None
    raw: dict[str, Any] = field(default_factory=dict)

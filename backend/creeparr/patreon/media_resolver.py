"""Turn a PostResource into the list of files we could download."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from creeparr.db.enums import MediaKind, MediaSource
from creeparr.patreon.models import MediaResource, MediaSpec, PostResource
from creeparr.providers.http import host_matches

VIDEO_EXT = {"mp4", "mov", "m4v", "webm", "mkv", "avi", "m3u8", "ts", "flv", "wmv"}
AUDIO_EXT = {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "wma"}
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "avif"}

EMBED_PROVIDERS = {
    "youtube": MediaSource.EMBED_YOUTUBE,
    "vimeo": MediaSource.EMBED_VIMEO,
}


@dataclass(frozen=True)
class CreatorPrefs:
    include_images: bool = False
    include_audio: bool = False
    include_attachments: bool = False

    def wants(self, kind: MediaKind) -> bool:
        if kind == MediaKind.VIDEO:
            return True
        if kind == MediaKind.IMAGE:
            return self.include_images
        if kind == MediaKind.AUDIO:
            return self.include_audio
        return self.include_attachments


def url_ext(url: str | None) -> str:
    if not url:
        return ""
    return PurePosixPath(urlparse(url).path).suffix.lstrip(".").lower()


def name_ext(name: str | None) -> str:
    if not name:
        return ""
    return PurePosixPath(name).suffix.lstrip(".").lower()


def is_patreon_url(url: str | None) -> bool:
    return host_matches(url, "patreon.com")


def is_hls_url(url: str | None) -> bool:
    if not url:
        return False
    u = urlparse(url)
    return u.path.lower().endswith(".m3u8") or "stream.mux.com" in u.netloc


def kind_from_hints(mimetype: str | None, *names: str | None) -> MediaKind | None:
    mt = (mimetype or "").lower()
    if mt.startswith("video/") or mt in ("application/vnd.apple.mpegurl", "application/x-mpegurl"):
        return MediaKind.VIDEO
    if mt.startswith("audio/"):
        return MediaKind.AUDIO
    if mt.startswith("image/"):
        return MediaKind.IMAGE
    for n in names:
        ext = name_ext(n) if n and "://" not in n else url_ext(n)
        if ext in VIDEO_EXT:
            return MediaKind.VIDEO
        if ext in AUDIO_EXT:
            return MediaKind.AUDIO
        if ext in IMAGE_EXT:
            return MediaKind.IMAGE
    return None


def embed_source(provider: str | None, url: str | None) -> MediaSource:
    p = (provider or "").strip().lower()
    if p in EMBED_PROVIDERS:
        return EMBED_PROVIDERS[p]
    host = urlparse(url or "").netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return MediaSource.EMBED_YOUTUBE
    if "vimeo.com" in host:
        return MediaSource.EMBED_VIMEO
    return MediaSource.EMBED_OTHER


def _post_file_kind(post: PostResource) -> MediaKind | None:
    pf = post.post_file or {}
    url = pf.get("url")
    name = pf.get("name")
    ptype = post.post_type or ""
    if ptype == "video_external_file":
        return MediaKind.VIDEO
    if ptype in ("audio_file", "podcast", "audio_embed"):
        return MediaKind.AUDIO
    if ptype == "image_file":
        # post_file duplicates the first gallery image; handled via `images`.
        return None
    hinted = kind_from_hints(pf.get("mimetype"), name, url)
    if hinted is None and is_hls_url(url):
        return MediaKind.VIDEO
    return hinted


def _media_kind(m: MediaResource) -> MediaKind:
    hinted = kind_from_hints(m.mimetype, m.file_name, m.download_url)
    if hinted:
        return hinted
    if m.relationship == "images" or m.image_urls:
        return MediaKind.IMAGE
    if m.relationship == "audio":
        return MediaKind.AUDIO
    return MediaKind.ATTACHMENT


def _ordered_images(post: PostResource) -> list[MediaResource]:
    images = [m for m in post.media if m.relationship == "images"]
    order = (post.post_metadata or {}).get("image_order")
    if isinstance(order, list) and order:
        rank = {str(i): n for n, i in enumerate(order)}
        images.sort(key=lambda m: rank.get(m.id, len(rank)))
    return images


def resolve_media(post: PostResource) -> list[MediaSpec]:
    """All downloadable files for a viewable post, in a stable order. `wanted` is decided later."""
    if not post.current_user_can_view:
        return []

    specs: list[MediaSpec] = []
    seen_urls: set[str] = set()
    counters: dict[MediaKind, int] = {}

    def add(spec: MediaSpec) -> None:
        if not spec.url or spec.url in seen_urls:
            return
        seen_urls.add(spec.url)
        counters[spec.kind] = counters.get(spec.kind, 0) + 1
        spec.order_index = counters[spec.kind]
        specs.append(spec)

    # 1. Embedded video (YouTube / Vimeo / other). Links back into patreon.com
    #    (collections, other posts) are navigation, not media.
    if post.embed_url and not is_patreon_url(post.embed_url):
        add(
            MediaSpec(
                media_key=f"embed:{post.id}",
                kind=MediaKind.VIDEO,
                source=embed_source(post.embed_provider, post.embed_url),
                url=post.embed_url,
                metadata={"provider": post.embed_provider, "embed": post.embed},
            )
        )

    # 2. The post's primary file (native video or audio)
    pf = post.post_file or {}
    if pf.get("url"):
        kind = _post_file_kind(post)
        if kind in (MediaKind.VIDEO, MediaKind.AUDIO):
            url = str(pf["url"])
            source = (
                MediaSource.NATIVE_HLS
                if kind == MediaKind.VIDEO and is_hls_url(url)
                else MediaSource.NATIVE_DIRECT
            )
            add(
                MediaSpec(
                    media_key=f"postfile:{post.id}",
                    kind=kind,
                    source=source,
                    url=url,
                    file_name=pf.get("name"),
                    mimetype=pf.get("mimetype"),
                    size_bytes=_int(pf.get("size_bytes") or pf.get("size")),
                    metadata={k: v for k, v in pf.items() if k not in ("url",)},
                )
            )

    # 3. Gallery images (ordered), then audio, attachments, then any other media.
    for m in _ordered_images(post):
        add(_spec_from_media(m, MediaKind.IMAGE, "media"))
    for m in post.media:
        if m.relationship == "audio":
            add(_spec_from_media(m, MediaKind.AUDIO, "media"))
    for m in post.media:
        if m.relationship == "attachments_media":
            kind = _media_kind(m)
            if kind not in (MediaKind.VIDEO, MediaKind.AUDIO):
                kind = MediaKind.ATTACHMENT
            add(_spec_from_media(m, kind, "attachment"))
    has_native_video = any(
        s.kind == MediaKind.VIDEO and s.media_key.startswith("postfile:") for s in specs
    )
    for m in post.media:
        if m.relationship == "media":
            kind = _media_kind(m)
            if kind == MediaKind.VIDEO and has_native_video:
                # The generic `media` entry is the same asset as post_file, usually with
                # a download link that 404s. Skip it.
                continue
            add(_spec_from_media(m, kind, "media"))

    return specs


def _spec_from_media(m: MediaResource, kind: MediaKind, prefix: str) -> MediaSpec:
    url = m.best_url or ""
    return MediaSpec(
        media_key=f"{prefix}:{m.id}",
        kind=kind,
        source=MediaSource.MEDIA_DOWNLOAD,
        url=url,
        file_name=m.file_name,
        mimetype=m.mimetype,
        size_bytes=m.size_bytes,
        metadata={
            "relationship": m.relationship,
            **({"metadata": m.metadata} if m.metadata else {}),
        },
    )


def _int(v) -> int | None:  # noqa: ANN001
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None

"""The path rules: a post's folder and a file's name in the library, from the naming settings.

Pure functions of a post's facts, so they work without a running download.
"""

from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Protocol

from creeparr.core.naming import render_template, sanitize_component, split_name
from creeparr.core.settings_service import NamingSettings
from creeparr.db.enums import EMBED_SOURCES, MediaKind, MediaSource


class NamingFacts(Protocol):
    """What the folder and file name templates can use."""

    creator_name: str
    creator_folder: str | None
    creator_vanity: str | None
    campaign_id: str
    post_ext_id: str
    post_title: str
    published_at: datetime | None
    first_seen_at: datetime | None
    post_type: str | None
    embed_provider: str | None
    kind: str
    order_index: int
    source: str
    remote_file_name: str | None


def template_values(facts: NamingFacts) -> dict[str, Any]:
    return {
        "creator": facts.creator_folder or facts.creator_name,
        "creator_vanity": facts.creator_vanity or facts.campaign_id,
        "campaign_id": facts.campaign_id,
        "title": facts.post_title or "untitled",
        "post_id": facts.post_ext_id,
        "published": (
            facts.published_at or facts.first_seen_at or datetime(1970, 1, 1, tzinfo=UTC)
        ),
        "post_type": facts.post_type or "",
        "media_kind": facts.kind,
        "media_index": facts.order_index,
        "embed_provider": facts.embed_provider or "",
    }


def post_folder(naming: NamingSettings, facts: NamingFacts) -> PurePosixPath:
    """The post's folder, relative to its provider's download root."""
    return render_template(
        naming.post_folder_template, template_values(facts), naming.max_component_length
    )


def file_name(naming: NamingSettings, facts: NamingFacts, ext: str) -> str:
    """The media file's name inside the post's folder."""
    stem, _ = split_name(facts.remote_file_name)
    if not stem or facts.source in EMBED_SOURCES or facts.source == MediaSource.NATIVE_HLS:
        stem = facts.post_title or "untitled"
        if facts.kind != MediaKind.VIDEO or facts.order_index > 1:
            stem = f"{stem} - {facts.kind} {facts.order_index}"
    filename = f"{stem}.{ext}" if ext else stem
    values = {**template_values(facts), "filename": filename, "ext": ext}
    rendered = render_template(naming.file_template, values, naming.max_component_length)
    return str(rendered) if len(rendered.parts) == 1 else sanitize_component(filename)


def container_ext(choice: str, provider: str) -> str:
    """A video's target container, per the container setting."""
    if choice in ("mp4", "mkv"):
        return choice
    return "mkv" if provider == "youtube" else "mp4"


def guess_ext(
    remote_file_name: str | None, url: str | None, mimetype: str | None, kind: str
) -> str:
    """The extension a provider's own file has, from its name, its URL or its mimetype."""
    _, ext = split_name(remote_file_name)
    if ext and ext != "m3u8":
        return ext
    if url:
        _, ext = split_name(PurePosixPath(url.split("?", 1)[0]).name)
        if ext and len(ext) <= 5 and ext != "m3u8":
            return ext
    if mimetype:
        guess = mimetypes.guess_extension(mimetype.split(";")[0].strip())
        if guess:
            return guess.lstrip(".")
    return {"video": "mp4", "audio": "mp3", "image": "jpg"}.get(kind, "bin")

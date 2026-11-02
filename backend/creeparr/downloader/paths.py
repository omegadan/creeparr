"""The path rules: a post's folder and a file's name in the library, from the naming settings.

Pure functions of a post's facts, so they work without a running download: the settings
page previews them on sample posts, and saving refuses a template they can't render.
"""

from __future__ import annotations

import mimetypes
import string
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol

from creeparr.core.naming import render_template, split_name
from creeparr.db.enums import EMBED_SOURCES, MediaKind, MediaSource

if TYPE_CHECKING:
    from creeparr.core.settings_service import NamingSettings


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


@dataclass(frozen=True)
class Token:
    """A `{name}` a template can use."""

    name: str
    description: str
    #: Its value for a post's media; None for the tokens only a file name has.
    value: Callable[[NamingFacts], Any] | None = None


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

TOKENS: tuple[Token, ...] = (
    Token(
        "creator",
        "The creator's folder name, or their name",
        lambda f: f.creator_folder or f.creator_name,
    ),
    Token(
        "creator_vanity",
        "The creator's handle on the site",
        lambda f: f.creator_vanity or f.campaign_id,
    ),
    Token("campaign_id", "The creator's id on the site", lambda f: f.campaign_id),
    Token("title", "The post's title", lambda f: f.post_title or "untitled"),
    Token("post_id", "The post's id on the site", lambda f: f.post_ext_id),
    Token(
        "published",
        "When the post was published, or first seen; add a format, e.g. {published:%Y-%m-%d}",
        lambda f: f.published_at or f.first_seen_at or _EPOCH,
    ),
    Token("post_type", "The kind of post, as the site names it", lambda f: f.post_type or ""),
    Token("media_kind", "video, audio or image", lambda f: f.kind),
    Token("media_index", "The file's place in the post, from 1", lambda f: f.order_index),
    Token(
        "embed_provider",
        "Who hosts an embedded video, e.g. YouTube",
        lambda f: f.embed_provider or "",
    ),
    Token(
        "filename", "File names only: the original name, or the post's title for streams and embeds"
    ),
    Token("ext", "File names only: the file's extension"),
)

_FOLDER_TOKENS = frozenset(t.name for t in TOKENS if t.value is not None)
_FILE_TOKENS = frozenset(t.name for t in TOKENS)


def template_values(facts: NamingFacts) -> dict[str, Any]:
    return {t.name: t.value(facts) for t in TOKENS if t.value is not None}


def post_folder(naming: NamingSettings, facts: NamingFacts) -> PurePosixPath:
    """The post's folder, relative to its provider's download root."""
    return render_template(
        naming.post_folder_template, template_values(facts), naming.max_component_length
    )


def file_name(naming: NamingSettings, facts: NamingFacts, ext: str) -> str:
    """The media file's name inside the post's folder. (The file name template can't make
    folders: saving refuses one with a slash.)"""
    values = _file_values(facts, ext)
    return str(render_template(naming.file_template, values, naming.max_component_length))


def _file_values(facts: NamingFacts, ext: str) -> dict[str, Any]:
    """What the file name template can use: the folder's tokens, `filename` and `ext`."""
    stem, _ = split_name(facts.remote_file_name)
    if not stem or facts.source in EMBED_SOURCES or facts.source == MediaSource.NATIVE_HLS:
        stem = facts.post_title or "untitled"
        if facts.kind != MediaKind.VIDEO or facts.order_index > 1:
            stem = f"{stem} - {facts.kind} {facts.order_index}"
    filename = f"{stem}.{ext}" if ext else stem
    return {**template_values(facts), "filename": filename, "ext": ext}


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


# ---- previewing and checking the templates ----------------------------------------------


@dataclass(frozen=True)
class Sample:
    """A made-up post's media, to show what the templates make of it."""

    label: str
    ext: str
    post_title: str = "Episode 12: Behind the scenes / Q&A"
    published_at: datetime | None = datetime(2026, 3, 14, 15, 9, tzinfo=UTC)
    first_seen_at: datetime | None = datetime(2026, 3, 15, 8, 0, tzinfo=UTC)
    kind: str = MediaKind.VIDEO
    order_index: int = 1
    source: str = MediaSource.NATIVE_DIRECT
    remote_file_name: str | None = None
    post_type: str | None = "video_external_file"
    embed_provider: str | None = None
    creator_name: str = "Example Creator"
    creator_folder: str | None = None
    creator_vanity: str | None = "examplecreator"
    campaign_id: str = "1234567"
    post_ext_id: str = "98765432"


# The cases where the rules differ: a file's own name, a name made from the title, an
# embed (always named from the title) and a post with no publish date.
SAMPLES: tuple[Sample, ...] = (
    Sample("Attached file", "mp4", remote_file_name="episode-12.mp4"),
    Sample("Second image", "jpg", kind=MediaKind.IMAGE, order_index=2, post_type="image_file"),
    Sample(
        "YouTube embed",
        "mp4",
        source=MediaSource.EMBED_YOUTUBE,
        embed_provider="YouTube",
        post_type="video_embed",
    ),
    Sample(
        "Undated post",
        "mp4",
        post_title="Bonus clip",
        published_at=None,
        remote_file_name="bonus.mp4",
        post_ext_id="98765433",
    ),
)


@dataclass(frozen=True)
class SamplePath:
    label: str
    post_folder: str
    file: str
    full_path: str


def preview(naming: NamingSettings) -> list[SamplePath]:
    """Where each sample's file would land under `naming`. Raises ValueError if a template
    can't be rendered."""
    out = []
    for sample in SAMPLES:
        folder = _rendering(lambda s=sample: post_folder(naming, s))
        name = _rendering(lambda s=sample: file_name(naming, s, s.ext))
        out.append(SamplePath(sample.label, str(folder), name, str(folder / name)))
    return out


def unknown_tokens(naming: NamingSettings) -> list[str]:
    """The `{names}` the templates use that aren't tokens there; they render as nothing."""
    found = _fields(naming.post_folder_template) - _FOLDER_TOKENS
    found |= _fields(naming.file_template) - _FILE_TOKENS
    return sorted(found)


def token_examples() -> list[tuple[Token, str]]:
    """Each token, with its value for the first sample that has one."""
    examples = []
    for token in TOKENS:
        for sample in SAMPLES:
            value = _file_values(sample, sample.ext)[token.name]
            if value not in ("", None):
                break
        examples.append((token, str(render_template(f"{{{token.name}}}", {token.name: value}))))
    return examples


def template_problem(template: str, *, file: bool) -> str | None:
    """Why `template` can't be used as the post folder (or file name) template, if it can't."""
    if file and "/" in template.replace("\\", "/"):
        return "The file name template can't create folders; put them in the post folder template"
    values = _file_values(SAMPLES[0], SAMPLES[0].ext)
    try:
        _rendering(lambda: render_template(template, values))
    except ValueError as exc:
        reason = str(exc)
        return reason[:1].upper() + reason[1:]
    return None


def _rendering[T](render: Callable[[], T]) -> T:
    """`render()`, with every way a template can fail to render raised as a ValueError."""
    try:
        return render()
    except ValueError:
        raise
    except IndexError:
        raise ValueError("Use a token's name in braces, e.g. {title}, not a number") from None
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError(f"A token can't be used that way: {exc}") from None


def _fields(template: str) -> set[str]:
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError:
        return set()
    names = set()
    for _, field, _, _ in parsed:
        if field:
            name = field.split(".", 1)[0].split("[", 1)[0]
            if name and not name.isdigit():
                names.add(name)
    return names

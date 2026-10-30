"""ffmpeg: embed post metadata and a cover into a video, and remux into another container."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from creeparr.config import EnvConfig

log = logging.getLogger(__name__)


class Ffmpeg(Protocol):
    """ffmpeg as downloads use it. Tests swap in a fake for the real program."""

    @property
    def location(self) -> str | None:
        """The program's path, or None when it isn't installed."""
        ...

    def remux(self, src: Path, dst: Path) -> bool: ...

    def embed(self, video: Path, metadata: dict[str, str], cover: Path | None) -> bool: ...


class SystemFfmpeg:
    """The ffmpeg program at the configured path, or else the one on the PATH."""

    def __init__(self, env: EnvConfig) -> None:
        self._env = env

    @property
    def location(self) -> str | None:
        return self._env.resolve_ffmpeg()

    def remux(self, src: Path, dst: Path) -> bool:
        ffmpeg = self.location
        return ffmpeg is not None and remux_container(ffmpeg, src, dst)

    def embed(self, video: Path, metadata: dict[str, str], cover: Path | None) -> bool:
        ffmpeg = self.location
        return ffmpeg is not None and embed_metadata(ffmpeg, video, metadata, cover)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(html: str | None, limit: int = 4000) -> str:
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    return _WS_RE.sub(" ", text).strip()[:limit]


def _run(cmd: list[str]) -> bool:
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=300)
        if res.returncode != 0:
            log.debug("ffmpeg embed failed: %s", res.stderr.decode("utf-8", "ignore")[-400:])
            return False
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("ffmpeg embed error: %s", exc)
        return False


def embed_metadata(
    ffmpeg: str,
    video: Path,
    metadata: dict[str, str],
    thumbnail: Path | None = None,
) -> bool:
    """Remux `video` in place, embedding metadata tags and (optionally) a cover image.

    Returns True if the file was rewritten. Falls back to metadata-only if attaching the
    cover fails, and leaves the original untouched if everything fails.
    """
    out = video.with_name(video.name + ".embed-tmp" + video.suffix)
    meta_args: list[str] = []
    for key, value in metadata.items():
        if value:
            meta_args += ["-metadata", f"{key}={value}"]

    cover_ok = (
        thumbnail is not None
        and thumbnail.exists()
        and video.suffix.lower()
        in (
            ".mp4",
            ".m4v",
            ".mov",
            ".mkv",
        )
    )
    attempts: list[list[str]] = []
    if cover_ok:
        attempts.append(
            [
                ffmpeg,
                "-y",
                "-i",
                str(video),
                "-i",
                str(thumbnail),
                "-map",
                "0",
                "-map",
                "1",
                "-c",
                "copy",
                "-disposition:v:1",
                "attached_pic",
                *meta_args,
                str(out),
            ]
        )
    base = [ffmpeg, "-y", "-i", str(video), "-map", "0", "-c", "copy", *meta_args, str(out)]
    attempts.append(base)

    for cmd in attempts:
        if _run(cmd) and out.exists() and out.stat().st_size > 0:
            out.replace(video)
            return True
        out.unlink(missing_ok=True)
    return False


def remux_container(ffmpeg: str, src: Path, dst: Path) -> bool:
    """Losslessly remux `src` into a different container `dst` (-c copy)."""
    ok = _run([ffmpeg, "-y", "-i", str(src), "-map", "0", "-c", "copy", str(dst)])
    if ok and dst.exists() and dst.stat().st_size > 0:
        return True
    dst.unlink(missing_ok=True)  # don't leave a partial file beside the fallback
    return False

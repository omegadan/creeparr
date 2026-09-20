"""Embed post metadata and a cover thumbnail into a downloaded video via ffmpeg."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

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

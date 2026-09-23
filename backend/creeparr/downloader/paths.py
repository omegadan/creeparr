"""Where a job's files go: folder and file names, reserved destinations, finalising."""

from __future__ import annotations

import contextlib
import logging
import mimetypes
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from creeparr.core.naming import render_template, sanitize_component, split_name, unique_path
from creeparr.db.enums import (
    MediaKind,
    MediaSource,
)
from creeparr.downloader.common import (
    EMBED_SOURCES,
    JobContext,
)
from creeparr.downloader.fs import (
    remove_tree,
    sha256_file,
)
from creeparr.downloader.handlers.base import DownloadResult
from creeparr.downloader.metadata import remux_container

log = logging.getLogger(__name__)


def _remove_partials(parts: list[Path]) -> None:
    """Drop .part files of a download that won't be resumed (cancelled or failed for good)."""
    for part in parts:
        part.unlink(missing_ok=True)


def _remove_tmp_dir(tmp_dir: Path) -> None:
    """Remove a job's temp dir, and its post folder too if that is now empty (a folder
    made before a date backfill moved the video elsewhere)."""
    remove_tree(tmp_dir)
    with contextlib.suppress(OSError):
        tmp_dir.parent.rmdir()  # fails, as intended, unless empty


class PathsMixin:
    """DownloadManager's naming and file-placement logic."""

    def _values(self, ctx: JobContext) -> dict[str, Any]:
        return {
            "creator": ctx.creator_folder or ctx.creator_name,
            "creator_vanity": ctx.creator_vanity or ctx.campaign_id,
            "campaign_id": ctx.campaign_id,
            "title": ctx.post_title or "untitled",
            "post_id": ctx.post_ext_id,
            "published": (
                ctx.post_published_at or ctx.post_first_seen or datetime(1970, 1, 1, tzinfo=UTC)
            ),
            "post_type": ctx.post_type or "",
            "media_kind": ctx.kind,
            "media_index": ctx.order_index,
            "embed_provider": ctx.embed_provider or "",
        }

    def _post_dir(self, ctx: JobContext) -> PurePosixPath:
        naming = self.settings.get().naming
        return render_template(
            naming.post_folder_template, self._values(ctx), naming.max_component_length
        )

    def _container_ext(self, ctx: JobContext) -> str:
        """Target container extension for a video, per the container setting."""
        choice = self.settings.get().downloads.container
        if choice in ("mp4", "mkv"):
            return choice
        return "mkv" if ctx.provider == "youtube" else "mp4"

    def _guess_ext(self, ctx: JobContext) -> str:
        _, ext = split_name(ctx.remote_file_name)
        if ext and ext != "m3u8":
            return ext
        if ctx.url:
            _, ext = split_name(PurePosixPath(ctx.url.split("?", 1)[0]).name)
            if ext and len(ext) <= 5 and ext != "m3u8":
                return ext
        if ctx.mimetype:
            guess = mimetypes.guess_extension(ctx.mimetype.split(";")[0].strip())
            if guess:
                return guess.lstrip(".")
        return {"video": "mp4", "audio": "mp3", "image": "jpg"}.get(ctx.kind, "bin")

    def _file_name(self, ctx: JobContext, ext: str) -> str:
        naming = self.settings.get().naming
        stem, _ = split_name(ctx.remote_file_name)
        if not stem or ctx.source in EMBED_SOURCES or ctx.source == MediaSource.NATIVE_HLS:
            stem = ctx.post_title or "untitled"
            if ctx.kind != MediaKind.VIDEO or ctx.order_index > 1:
                stem = f"{stem} - {ctx.kind} {ctx.order_index}"
        filename = f"{stem}.{ext}" if ext else stem
        values = {**self._values(ctx), "filename": filename, "ext": ext}
        rendered = render_template(naming.file_template, values, naming.max_component_length)
        return str(rendered) if len(rendered.parts) == 1 else sanitize_component(filename)

    def _reserve_dest(self, ctx: JobContext, path: Path) -> Path:
        """A free file name, also free of names other running jobs are still writing."""
        with self._dest_lock:
            taken = set().union(*self._reserved_dests.values())
            dest = unique_path(path, taken)
            self._reserved_dests.setdefault(ctx.job_id, set()).add(dest)
            return dest

    def _remux_to(
        self, ctx: JobContext, src: Path, dest: Path, compute_sha256: bool
    ) -> DownloadResult:

        ffmpeg = self.env.resolve_ffmpeg()
        if ffmpeg and remux_container(ffmpeg, src, dest):
            src.unlink(missing_ok=True)
        else:
            # No ffmpeg or remux failed: keep the original file under its own extension.
            fallback = dest.with_suffix(src.suffix)
            src.replace(fallback)
            dest = fallback
            log.warning("[job %s] container remux unavailable; kept %s", ctx.job_id, dest.name)
        size = dest.stat().st_size
        sha = sha256_file(dest) if compute_sha256 else None
        return DownloadResult(dest, size, sha)

    def _finalise_ytdlp(
        self, ctx: JobContext, post_dir: Path, produced: Path, compute_sha256: bool
    ) -> DownloadResult:
        from creeparr.downloader.fs import atomic_replace

        ext = produced.suffix.lstrip(".").lower() or "mp4"
        dest = self._reserve_dest(ctx, post_dir / self._file_name(ctx, ext))
        atomic_replace(produced, dest)
        size = dest.stat().st_size
        sha = sha256_file(dest) if compute_sha256 else None
        return DownloadResult(dest, size, sha)

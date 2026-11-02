"""Plain HTTP download with .part resume."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from creeparr.core.downloads import FailureKind
from creeparr.core.settings_service import SettingsService
from creeparr.db.enums import JobStage, MediaKind
from creeparr.downloader.fs import atomic_replace
from creeparr.downloader.handlers.base import (
    DownloadCancelled,
    DownloadFailed,
    Fetched,
    FetchPlan,
    MediaSnapshot,
    Placement,
    ProgressReporter,
)
from creeparr.downloader.longwork import LongWork
from creeparr.downloader.metadata import Ffmpeg
from creeparr.downloader.paths import guess_ext
from creeparr.providers.errors import TransportFailure
from creeparr.providers.http import TransportResponse

log = logging.getLogger(__name__)

Offload = Callable[..., Awaitable[Any]]


class MediaFetcher(Protocol):
    async def stream(self, url: str, *, range_start: int = 0) -> TransportResponse: ...


@dataclass
class DirectResult:
    path: Path
    size: int
    sha256: str | None = None


class DirectHandler:
    """A provider's own files, downloaded over HTTP. The final name is reserved before the
    first byte, so a retry resumes the `.part` file there."""

    learns_date = False

    def __init__(self, settings: SettingsService, ffmpeg: Ffmpeg, long_work: LongWork) -> None:
        self._settings = settings
        self._ffmpeg = ffmpeg
        self._long = long_work

    async def fetch(
        self,
        media: MediaSnapshot,
        plan: FetchPlan,
        place: Placement,
        progress: ProgressReporter,
    ) -> Fetched:
        d = self._settings.get().downloads
        native = guess_ext(media.remote_file_name, media.url, media.mimetype, media.kind)
        target = plan.container if media.kind == MediaKind.VIDEO else native
        dest = place.reserve(target)
        parts = [_part(dest)]  # kept only for a retry's resume
        progress.set_stage(JobStage.DOWNLOADING)
        try:
            if native == target:
                res = await download_direct(
                    plan.session,
                    media.url or "",
                    dest,
                    progress,
                    compute_sha256=d.compute_sha256,
                    offload=self._long.run,
                )
                return Fetched(res.path, sha256=res.sha256)
            # A video in another container: download it beside the target, then remux.
            src = dest.with_name(dest.stem + f".src.{native}")
            parts.append(_part(src))
            res = await download_direct(
                plan.session,
                media.url or "",
                src,
                progress,
                compute_sha256=False,
                offload=self._long.run,
            )
            return Fetched(await self._long.run(self._remux, media, res.path, dest))
        except DownloadCancelled:
            _remove(parts)
            raise
        except DownloadFailed as exc:
            if not exc.kind.retried:
                _remove(parts)
            raise

    def _remux(self, media: MediaSnapshot, src: Path, dest: Path) -> Path:
        if self._ffmpeg.remux(src, dest):
            src.unlink(missing_ok=True)
            return dest
        # No ffmpeg or remux failed: keep the original file under its own extension.
        fallback = dest.with_suffix(src.suffix)
        src.replace(fallback)
        log.warning("%s container remux unavailable; kept %s", media.label, fallback.name)
        return fallback


def _part(path: Path) -> Path:
    return path.with_name(path.name + ".part")


def _remove(parts: list[Path]) -> None:
    for part in parts:
        part.unlink(missing_ok=True)


def _hash_existing(path: Path) -> hashlib._Hash:  # type: ignore[name-defined]
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h


def _content_range_start(value: str | None) -> int | None:
    # "bytes 1000-1999/2000" -> 1000
    if not value or not value.startswith("bytes ") or "-" not in value:
        return None
    start = value[6:].split("-", 1)[0].strip()
    return int(start) if start.isdigit() else None


def _content_range_total(value: str | None) -> int | None:
    # "bytes 1000-1999/2000"
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else None


async def download_direct(
    client: MediaFetcher,
    url: str,
    dest: Path,
    reporter: ProgressReporter,
    *,
    compute_sha256: bool = True,
    offload: Offload = asyncio.to_thread,
) -> DirectResult:
    """Download `url` to `dest` through `dest.part`, resuming a `.part` left by an earlier
    try. `offload` runs the blocking re-hash of a resumed part (the long-work pool)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    existing = part.stat().st_size if part.exists() else 0

    try:
        resp = await client.stream(url, range_start=existing)
    except TransportFailure as exc:
        raise DownloadFailed(FailureKind.RETRYABLE, str(exc), "network") from exc

    hasher = None
    total: int | None = None
    try:
        status = resp.status
        if status == 416 and existing > 0:
            # Our range starts past the end. That means "already complete" only if the
            # .part is exactly the server's size; otherwise the file changed, so restart.
            total = _content_range_total(resp.headers.get("content-range"))
            await resp.aclose()
            if total is not None and total != existing:
                part.unlink(missing_ok=True)
                raise DownloadFailed(
                    FailureKind.RETRYABLE,
                    f"partial file ({existing} bytes) doesn't match the server's {total}",
                    "resume",
                )
            size = existing
            sha = (await offload(_hash_existing, part)).hexdigest() if compute_sha256 else None
            atomic_replace(part, dest)
            return DirectResult(dest, size, sha)
        if status == 206 and existing > 0:
            if _content_range_start(resp.headers.get("content-range")) not in (None, existing):
                # Appending bytes from another offset would corrupt the file.
                await resp.aclose()
                part.unlink(missing_ok=True)
                raise DownloadFailed(
                    FailureKind.RETRYABLE, "server resumed at the wrong offset", "resume"
                )
            mode = "ab"
            total = _content_range_total(resp.headers.get("content-range"))
            if compute_sha256:
                hasher = await offload(_hash_existing, part)
        elif status == 200:
            mode = "wb"
            existing = 0
            cl = resp.headers.get("content-length")
            total = int(cl) if cl and cl.isdigit() else None
            if compute_sha256:
                hasher = hashlib.sha256()
        elif status in (401, 403):
            raise DownloadFailed(
                FailureKind.RETRYABLE, f"HTTP {status} (expired or unauthorised URL)", "expired_url"
            )
        elif status == 404 or status == 410:
            raise DownloadFailed(FailureKind.FILE_GONE, f"HTTP {status}: file gone", "not_found")
        elif status == 429 or status >= 500:
            raise DownloadFailed(FailureKind.RETRYABLE, f"HTTP {status}", "server")
        else:
            raise DownloadFailed(FailureKind.RETRYABLE, f"unexpected HTTP {status}", "http")

        downloaded = existing
        reporter.set_stage(JobStage.DOWNLOADING)
        with part.open(mode) as f:
            async for chunk in resp.aiter_bytes(1 << 16):
                if not chunk:
                    continue
                f.write(chunk)
                if hasher is not None:
                    hasher.update(chunk)
                downloaded += len(chunk)
                reporter.update(downloaded=downloaded, total=total)
    except TransportFailure as exc:
        raise DownloadFailed(FailureKind.RETRYABLE, str(exc), "network") from exc
    finally:
        await resp.aclose()

    size = part.stat().st_size
    if total is not None and size != total:
        raise DownloadFailed(
            FailureKind.RETRYABLE, f"size mismatch: got {size}, expected {total}", "truncated"
        )
    reporter.update(downloaded=size, total=total, force=True)
    atomic_replace(part, dest)
    return DirectResult(dest, size, hasher.hexdigest() if hasher is not None else None)

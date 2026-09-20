"""Plain HTTP download with .part resume."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from patrearr.downloader.fs import atomic_replace
from patrearr.downloader.handlers.base import (
    DownloadResult,
    PermanentDownloadError,
    ProgressReporter,
    RetryableDownloadError,
)
from patrearr.patreon.client import PatreonClient
from patrearr.patreon.errors import TransportFailure

log = logging.getLogger(__name__)


def _hash_existing(path: Path) -> hashlib._Hash:  # type: ignore[name-defined]
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h


def _content_range_total(value: str | None) -> int | None:
    # "bytes 1000-1999/2000"
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else None


async def download_direct(
    client: PatreonClient,
    url: str,
    dest: Path,
    reporter: ProgressReporter,
    *,
    compute_sha256: bool = True,
) -> DownloadResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    existing = part.stat().st_size if part.exists() else 0

    try:
        resp = await client.stream(url, range_start=existing, headers=client.media_headers())
    except TransportFailure as exc:
        raise RetryableDownloadError(str(exc), "network") from exc

    hasher = None
    total: int | None = None
    try:
        status = resp.status
        if status == 416 and existing > 0:
            # Server says our range is past the end: the .part is probably complete.
            await resp.aclose()
            size = existing
            sha = (
                (await asyncio.to_thread(_hash_existing, part)).hexdigest()
                if compute_sha256
                else None
            )
            atomic_replace(part, dest)
            return DownloadResult(dest, size, sha)
        if status == 206 and existing > 0:
            mode = "ab"
            total = _content_range_total(resp.headers.get("content-range"))
            if compute_sha256:
                hasher = await asyncio.to_thread(_hash_existing, part)
        elif status == 200:
            mode = "wb"
            existing = 0
            cl = resp.headers.get("content-length")
            total = int(cl) if cl and cl.isdigit() else None
            if compute_sha256:
                hasher = hashlib.sha256()
        elif status in (401, 403):
            raise RetryableDownloadError(
                f"HTTP {status} (expired or unauthorised URL)", "expired_url"
            )
        elif status == 404 or status == 410:
            raise PermanentDownloadError(f"HTTP {status}: file gone", "not_found")
        elif status == 429 or status >= 500:
            raise RetryableDownloadError(f"HTTP {status}", "server")
        else:
            raise RetryableDownloadError(f"unexpected HTTP {status}", "http")

        downloaded = existing
        reporter.set_stage("downloading")
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
        raise RetryableDownloadError(str(exc), "network") from exc
    finally:
        await resp.aclose()

    size = part.stat().st_size
    if total is not None and size != total:
        raise RetryableDownloadError(f"size mismatch: got {size}, expected {total}", "truncated")
    reporter.update(downloaded=size, total=total, force=True)
    atomic_replace(part, dest)
    return DownloadResult(dest, size, hasher.hexdigest() if hasher is not None else None)

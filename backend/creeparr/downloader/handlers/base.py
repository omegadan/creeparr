"""The handler seam: what a handler is given and what it hands back.

A handler brings one media item's file to disk, over plain HTTP or with yt-dlp. Either it
reserves the file's final name before it starts (so a direct download can resume its `.part`
file there), or it downloads into the scratch folder and returns the file, with what it
learned about the post, for the download run to file in the post's folder.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from creeparr.core.downloads import FailureKind
from creeparr.providers.media import MediaSession


class DownloadCancelled(Exception):
    pass


class DownloadFailed(Exception):
    def __init__(self, kind: FailureKind, message: str, label: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.label = label


@dataclass(frozen=True)
class MediaSnapshot:
    """A download's media item, with its post and creator, as read when the download starts
    (and again after it renews the post's links)."""

    job_id: int
    attempt: int
    provider: str
    media_id: int
    kind: str
    source: str
    url: str | None
    remote_file_name: str | None
    mimetype: str | None
    order_index: int
    remote_metadata: Mapping[str, Any]
    post_id: int
    post_ext_id: str
    post_title: str
    published_at: datetime | None
    first_seen_at: datetime | None
    post_type: str | None
    embed_provider: str | None
    urls_fetched_at: datetime | None
    can_view: bool
    creator_id: int
    creator_name: str
    creator_folder: str | None
    creator_vanity: str | None
    campaign_id: str

    @property
    def label(self) -> str:
        """How log lines name this download."""
        return f"[job {self.job_id} media {self.media_id}]"


@dataclass(frozen=True)
class FetchPlan:
    """How a download fetches its file, worked out by the run when it starts."""

    #: The session of whoever hosts the file: YouTube for its videos, else the poster.
    session: MediaSession
    #: The video container to end up in: the setting, or what "auto" means for the creator.
    container: str
    #: The failure message when the file's site asks for a signed-in session.
    sign_in_message: str


@dataclass(frozen=True)
class Learned:
    """What a download found out about its post: yt-dlp knows an undated video's date."""

    published_at: datetime | None = None
    description: str | None = None


@dataclass(frozen=True)
class Fetched:
    path: Path
    #: False: the file is in the scratch folder, and the run files it in the post's folder.
    placed: bool = True
    #: Set when the handler hashed the file while writing it; otherwise the run hashes it.
    sha256: str | None = None
    learned: Learned | None = None


class Placement(Protocol):
    def reserve(self, ext: str) -> Path:
        """The file's final path, kept from every other running download. Raises
        RuntimeError while the post's date is still to be learned: use scratch() then."""
        ...

    def scratch(self) -> Path:
        """This download's scratch folder, created if needed. It is kept after a failure that
        will be retried, so the next attempt can resume."""
        ...


class Handler(Protocol):
    #: Learns an undated post's date while downloading, so the post's folder waits for it.
    learns_date: bool

    async def fetch(
        self,
        media: MediaSnapshot,
        plan: FetchPlan,
        place: Placement,
        progress: ProgressReporter,
    ) -> Fetched:
        """Bring the media's file to disk, as `plan` says. Raises DownloadFailed or
        DownloadCancelled.

        Partial files it makes outside the scratch folder are kept after a failure that will
        be retried, and removed after any other failure or a cancel. Blocking work runs in
        the long-work pool.
        """
        ...


class ProgressReporter:
    """Throttles progress updates and carries the cancellation flag (thread-safe)."""

    def __init__(self, callback: Callable[[dict[str, Any]], None], min_interval: float = 1.0):
        self._cb = callback
        self._min = min_interval
        self._last_emit = 0.0
        self._last_bytes = 0
        self._last_bytes_ts = time.monotonic()
        self.cancel_event = threading.Event()
        self.stage: str | None = None

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise DownloadCancelled()

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self._emit({"stage": stage}, force=True)

    def update(
        self,
        *,
        downloaded: int | None = None,
        total: int | None = None,
        speed: float | None = None,
        eta: float | None = None,
        force: bool = False,
    ) -> None:
        self.check_cancel()
        now = time.monotonic()
        if speed is None and downloaded is not None:
            dt = now - self._last_bytes_ts
            if dt >= 0.5:
                speed = (downloaded - self._last_bytes) / dt
                self._last_bytes = downloaded
                self._last_bytes_ts = now
        percent = None
        if downloaded is not None and total:
            percent = min(100.0, downloaded * 100.0 / total)
        if eta is None and speed and total and downloaded is not None and speed > 0:
            eta = max(0.0, (total - downloaded) / speed)
        self._emit(
            {
                "bytes_downloaded": downloaded,
                "total_bytes": total,
                "speed_bps": int(speed) if speed is not None else None,
                "eta_seconds": int(eta) if eta is not None else None,
                "progress_percent": percent,
                "stage": self.stage,
            },
            force=force,
        )

    def _emit(self, data: dict[str, Any], *, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < self._min:
            return
        self._last_emit = now
        self._cb({k: v for k, v in data.items() if v is not None})

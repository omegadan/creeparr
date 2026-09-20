"""Shared handler types."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DownloadCancelled(Exception):
    pass


class RetryableDownloadError(Exception):
    def __init__(self, message: str, error_class: str = "retryable") -> None:
        super().__init__(message)
        self.error_class = error_class


class PermanentDownloadError(Exception):
    def __init__(self, message: str, error_class: str = "permanent") -> None:
        super().__init__(message)
        self.error_class = error_class


@dataclass
class DownloadResult:
    path: Path
    size: int
    sha256: str | None = None
    metadata_embedded: bool = False


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

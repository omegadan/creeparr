"""Where a running download's progress goes: a `job.progress` event and the job's row."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any

from creeparr.core.events import EventBus
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import JobStatus
from creeparr.db.models import DownloadJob
from creeparr.downloader.handlers.base import ProgressReporter

log = logging.getLogger(__name__)

_FIELDS = (
    "progress_percent",
    "bytes_downloaded",
    "total_bytes",
    "speed_bps",
    "eta_seconds",
    "stage",
)


class JobProgress:
    def __init__(self, session_factory: SessionFactory, bus: EventBus) -> None:
        self._factory = session_factory
        self.bus = bus

    def reporter(self, job_id: int) -> ProgressReporter:
        """A reporter for one download; its updates are throttled to about one a second."""
        return ProgressReporter(functools.partial(self._report, job_id))

    def _report(self, job_id: int, data: dict[str, Any]) -> None:
        self.bus.publish("job.progress", {"id": job_id, **data})
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Direct downloads report from the event loop; a blocking SQLite write here
            # would stall the whole server while the scanner holds the write lock.
            loop.run_in_executor(None, self._write, job_id, data)
        else:
            self._write(job_id, data)  # yt-dlp's hooks run in its own thread

    def _write(self, job_id: int, data: dict[str, Any]) -> None:
        try:
            with session_scope(self._factory) as s:
                job = s.get(DownloadJob, job_id)
                if job is None or job.status != JobStatus.RUNNING:
                    return  # finished meanwhile; don't overwrite its final state
                for key in _FIELDS:
                    if key in data:
                        setattr(job, key, data[key])
        except Exception:  # noqa: BLE001
            log.debug("progress write failed", exc_info=True)

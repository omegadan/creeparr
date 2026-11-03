"""Reporting a running download's progress, which must never be what fails the download."""

from __future__ import annotations

from creeparr.downloader.handlers.base import ProgressReporter
from creeparr.downloader.progress import JobProgress
from tests.test_lifecycle import RecordingBus


def test_estimated_sizes_are_reported_as_whole_bytes(session_factory):
    # yt-dlp's total_bytes_estimate is a float; the job.progress event counts whole bytes.
    bus = RecordingBus()
    reporter = JobProgress(session_factory, bus).reporter(1)
    reporter.update(downloaded=1024.0, total=110961532.62962963, force=True)
    ((name, data),) = bus.events
    assert name == "job.progress"
    assert data["bytes_downloaded"] == 1024 and data["total_bytes"] == 110961532


def test_a_progress_report_that_fails_does_not_fail_the_download():
    def broken(_data):
        raise ValueError("can't report")

    reporter = ProgressReporter(broken)
    reporter.update(downloaded=1, total=2, force=True)  # no exception
    reporter.set_stage("downloading")

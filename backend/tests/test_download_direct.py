from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from patreonarr.downloader.handlers.base import (
    PermanentDownloadError,
    ProgressReporter,
    RetryableDownloadError,
)
from patreonarr.downloader.handlers.direct import download_direct

URL = "https://c10.patreonusercontent.com/video.mp4?token=x"


def reporter() -> tuple[ProgressReporter, list]:
    events: list = []
    return ProgressReporter(events.append, min_interval=0), events


@pytest.mark.asyncio
async def test_full_download(client, respx_mock, tmp_path: Path):
    body = b"x" * 200_000
    respx_mock.get(URL).mock(
        return_value=httpx.Response(200, content=body, headers={"content-length": str(len(body))})
    )
    rep, events = reporter()
    dest = tmp_path / "video.mp4"
    res = await download_direct(client, URL, dest, rep)
    assert res.path == dest and dest.read_bytes() == body
    assert res.size == len(body) and res.sha256 == hashlib.sha256(body).hexdigest()
    assert not dest.with_name("video.mp4.part").exists()
    assert any(e.get("progress_percent") == 100.0 for e in events)


@pytest.mark.asyncio
async def test_resume_with_206(client, respx_mock, tmp_path: Path):
    body = b"0123456789" * 1000
    dest = tmp_path / "v.mp4"
    part = tmp_path / "v.mp4.part"
    part.write_bytes(body[:4000])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=4000-"
        return httpx.Response(
            206,
            content=body[4000:],
            headers={"content-range": f"bytes 4000-{len(body) - 1}/{len(body)}"},
        )

    respx_mock.get(URL).mock(side_effect=handler)
    rep, _ = reporter()
    res = await download_direct(client, URL, dest, rep)
    assert dest.read_bytes() == body
    assert res.sha256 == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_server_ignores_range_restarts(client, respx_mock, tmp_path: Path):
    body = b"abcdef" * 100
    dest = tmp_path / "v.bin"
    dest.with_name("v.bin.part").write_bytes(b"garbage")
    respx_mock.get(URL).mock(return_value=httpx.Response(200, content=body))
    rep, _ = reporter()
    await download_direct(client, URL, dest, rep)
    assert dest.read_bytes() == body


@pytest.mark.asyncio
async def test_truncated_is_retryable(client, respx_mock, tmp_path: Path):
    respx_mock.get(URL).mock(
        return_value=httpx.Response(200, content=b"abc", headers={"content-length": "10"})
    )
    rep, _ = reporter()
    with pytest.raises(RetryableDownloadError) as ei:
        await download_direct(client, URL, tmp_path / "v.bin", rep)
    assert ei.value.error_class == "truncated"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,exc,klass",
    [
        (403, RetryableDownloadError, "expired_url"),
        (404, PermanentDownloadError, "not_found"),
        (503, RetryableDownloadError, "server"),
    ],
)
async def test_status_classification(client, respx_mock, tmp_path: Path, status, exc, klass):
    respx_mock.get(URL).mock(return_value=httpx.Response(status, content=b""))
    rep, _ = reporter()
    with pytest.raises(exc) as ei:
        await download_direct(client, URL, tmp_path / "v.bin", rep)
    assert ei.value.error_class == klass


@pytest.mark.asyncio
async def test_cancel_mid_stream(client, respx_mock, tmp_path: Path):
    from patreonarr.downloader.handlers.base import DownloadCancelled

    async def stream():
        for _ in range(50):
            yield b"y" * 1000

    respx_mock.get(URL).mock(return_value=httpx.Response(200, content=stream()))
    rep, _ = reporter()
    rep.cancel_event.set()
    with pytest.raises(DownloadCancelled):
        await download_direct(client, URL, tmp_path / "v.bin", rep)

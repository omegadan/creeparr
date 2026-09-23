from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from creeparr.downloader.handlers.base import (
    PermanentDownloadError,
    ProgressReporter,
    RetryableDownloadError,
)
from creeparr.downloader.handlers.direct import download_direct

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
    from creeparr.downloader.handlers.base import DownloadCancelled

    async def stream():
        for _ in range(50):
            yield b"y" * 1000

    respx_mock.get(URL).mock(return_value=httpx.Response(200, content=stream()))
    rep, _ = reporter()
    rep.cancel_event.set()
    with pytest.raises(DownloadCancelled):
        await download_direct(client, URL, tmp_path / "v.bin", rep)


def test_try_hardlink_dedupe(tmp_path):
    from creeparr.downloader.fs import try_hardlink

    src = tmp_path / "a.bin"
    src.write_bytes(b"same-content")
    dst = tmp_path / "b.bin"
    dst.write_bytes(b"same-content")
    assert dst.stat().st_ino != src.stat().st_ino
    assert try_hardlink(dst, src) is True
    assert dst.stat().st_ino == src.stat().st_ino  # now linked
    assert dst.read_bytes() == b"same-content"


def test_embed_metadata_roundtrip(tmp_path):
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        import pytest

        pytest.skip("ffmpeg not available")
    from creeparr.downloader.metadata import embed_metadata

    video = tmp_path / "v.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x48:rate=5",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        capture_output=True,
    )
    assert embed_metadata(ffmpeg, video, {"title": "My Title", "artist": "Me"}, None) is True
    out = subprocess.run(
        [shutil.which("ffprobe"), "-v", "quiet", "-show_format", str(video)],
        capture_output=True,
        text=True,
    ).stdout
    assert "title=My Title" in out


def test_set_times(tmp_path):
    from datetime import UTC, datetime

    from creeparr.downloader.fs import set_times

    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    when = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    set_times(f, when)
    set_times(tmp_path, when)
    assert abs(f.stat().st_mtime - when.timestamp()) < 2
    assert abs(tmp_path.stat().st_mtime - when.timestamp()) < 2
    set_times(f, None)  # no-op, must not raise


def test_failed_remux_leaves_no_partial_output(tmp_path):
    # A remux that dies part-way must not leave a truncated file next to the fallback.
    import stat

    from creeparr.downloader.metadata import remux_container

    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text('#!/bin/sh\nfor last; do :; done\necho partial > "$last"\nexit 1\n')
    ffmpeg.chmod(ffmpeg.stat().st_mode | stat.S_IEXEC)
    src, dst = tmp_path / "in.webm", tmp_path / "out.mp4"
    src.write_bytes(b"data")
    assert remux_container(str(ffmpeg), src, dst) is False
    assert not dst.exists()


@pytest.mark.asyncio
async def test_416_with_a_mismatched_size_restarts(client, respx_mock, tmp_path: Path):
    # 416 used to mean "the .part is complete" unconditionally, so a .part from a file
    # that has since changed (or was cut short) became the finished download.
    part = tmp_path / "v.mp4.part"
    part.write_bytes(b"x" * 500)
    respx_mock.get(URL).mock(
        return_value=httpx.Response(416, headers={"content-range": "bytes */2000"})
    )
    rep, _ = reporter()
    with pytest.raises(RetryableDownloadError):
        await download_direct(client, URL, tmp_path / "v.mp4", rep)
    assert not part.exists() and not (tmp_path / "v.mp4").exists()


@pytest.mark.asyncio
async def test_416_with_matching_size_completes(client, respx_mock, tmp_path: Path):
    part = tmp_path / "v.mp4.part"
    part.write_bytes(b"x" * 2000)
    respx_mock.get(URL).mock(
        return_value=httpx.Response(416, headers={"content-range": "bytes */2000"})
    )
    rep, _ = reporter()
    res = await download_direct(client, URL, tmp_path / "v.mp4", rep)
    assert res.size == 2000 and (tmp_path / "v.mp4").exists()


@pytest.mark.asyncio
async def test_206_from_the_wrong_offset_restarts(client, respx_mock, tmp_path: Path):
    part = tmp_path / "v.mp4.part"
    part.write_bytes(b"a" * 4000)
    respx_mock.get(URL).mock(
        return_value=httpx.Response(
            206, content=b"b" * 100, headers={"content-range": "bytes 0-99/10000"}
        )
    )
    rep, _ = reporter()
    with pytest.raises(RetryableDownloadError):
        await download_direct(client, URL, tmp_path / "v.mp4", rep)
    assert not part.exists()

"""yt-dlp as a library: HLS (Mux) streams and YouTube/Vimeo embeds."""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.utils import DownloadCancelled as YtDlpCancelled
from yt_dlp.utils import DownloadError, UnsupportedError

from creeparr.core.downloads import FailureKind, mentions_drm
from creeparr.core.settings_service import SettingsService
from creeparr.db.enums import MediaSource
from creeparr.downloader.handlers.base import (
    DownloadCancelled,
    DownloadFailed,
    Fetched,
    Learned,
    MediaSnapshot,
    Placement,
    ProgressReporter,
)
from creeparr.downloader.longwork import LongWork
from creeparr.downloader.metadata import Ffmpeg
from creeparr.downloader.paths import container_ext
from creeparr.providers.registry import ProviderRegistry

log = logging.getLogger("creeparr.ytdlp")

_PERMANENT_PATTERNS = (
    "private video",
    "video unavailable",
    "this video is unavailable",
    "is unavailable",
    "has been removed",
    "unsupported url",
    "http error 404",
    "http error 410",
    "does not exist",
    "is not available",
    "no video formats found",
    "requested format is not available",
    "members-only",
    "sign in to confirm your age",
    "age-restricted",
    "this video is private",
    "video has been deleted",
    "account associated with this video has been terminated",
)


@dataclass
class YtDlpOptions:
    headers: dict[str, str]
    cookiefile: str | None
    video_format: str
    container: str
    ffmpeg_location: str | None
    fragment_concurrency: int
    impersonate: bool = False
    remote_components: bool = True


class _LoggerAdapter:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug] "):
            return
        log.debug("%s %s", self.prefix, msg)

    def info(self, msg: str) -> None:
        log.info("%s %s", self.prefix, msg)

    def warning(self, msg: str) -> None:
        log.warning("%s %s", self.prefix, msg)

    def error(self, msg: str) -> None:
        log.error("%s %s", self.prefix, msg)


JS_RUNTIMES = ("deno", "node", "bun", "quickjs")


def available_js_runtimes() -> dict[str, dict[str, str]]:
    """JS runtimes yt-dlp may use (YouTube needs one). Empty dict keeps yt-dlp's default."""
    found: dict[str, dict[str, str]] = {}
    for name in JS_RUNTIMES:
        path = shutil.which(name)
        if path:
            found[name] = {"path": path}
    return found


def normalise_vimeo_url(url: str) -> str:
    """Turn `https://vimeo.com/123/abc` into the player URL yt-dlp resolves reliably."""
    u = urlparse(url)
    host = u.netloc.lower()
    if "vimeo.com" not in host or "player.vimeo.com" in host:
        return url
    parts = [p for p in u.path.split("/") if p]
    if not parts or not parts[0].isdigit():
        return url
    vid = parts[0]
    h = parse_qs(u.query).get("h", [None])[0]
    if h is None and len(parts) > 1 and re.fullmatch(r"[0-9a-f]{6,12}", parts[1]):
        h = parts[1]
    player = f"https://player.vimeo.com/video/{vid}"
    return f"{player}?h={h}" if h else player


def classify_ytdlp_error(exc: Exception) -> DownloadFailed:
    if isinstance(exc, UnsupportedError):
        return DownloadFailed(FailureKind.UNSUPPORTED, f"unsupported URL: {exc}", "unsupported")
    msg = str(exc)
    low = msg.lower()
    if "sign in" in low or "use --cookies" in low or "cookies" in low or "confirm your age" in low:
        return DownloadFailed(
            FailureKind.NEEDS_COOKIES,
            "YouTube requires a signed-in session for this video. Add YouTube cookies in "
            "Settings -> Accounts -> YouTube, then retry.",
            "needs_cookies",
        )
    if mentions_drm(msg):
        return DownloadFailed(FailureKind.DRM, msg, "drm")
    if any(p in low for p in _PERMANENT_PATTERNS):
        return DownloadFailed(FailureKind.UNAVAILABLE, msg, "unavailable")
    return DownloadFailed(FailureKind.RETRYABLE, msg, "ytdlp")


def run_ytdlp(
    url: str, tmp_dir: Path, opts: YtDlpOptions, reporter: ProgressReporter, label: str
) -> tuple[Path, dict[str, Any]]:
    """Blocking. Downloads `url` into `tmp_dir` and returns the produced file."""
    tmp_dir.mkdir(parents=True, exist_ok=True)

    def progress_hook(d: dict[str, Any]) -> None:
        if reporter.cancel_event.is_set():
            raise YtDlpCancelled("cancelled by user")
        status = d.get("status")
        if status == "downloading":
            reporter.stage = "downloading"
            reporter.update(
                downloaded=d.get("downloaded_bytes"),
                total=d.get("total_bytes") or d.get("total_bytes_estimate"),
                speed=d.get("speed"),
                eta=d.get("eta"),
            )
        elif status == "finished":
            reporter.update(
                downloaded=d.get("total_bytes") or d.get("downloaded_bytes"),
                total=d.get("total_bytes") or d.get("downloaded_bytes"),
                force=True,
            )

    def pp_hook(d: dict[str, Any]) -> None:
        if reporter.cancel_event.is_set():
            raise YtDlpCancelled("cancelled by user")
        if d.get("status") == "started":
            reporter.set_stage("merging")

    ydl_opts: dict[str, Any] = {
        # Fixed name: generic HLS ids/titles can be hundreds of bytes and overflow NAME_MAX.
        "outtmpl": str(tmp_dir / "media.%(ext)s"),
        "format": opts.video_format,
        "merge_output_format": opts.container,
        "http_headers": opts.headers,
        "continuedl": True,
        "retries": 3,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": opts.fragment_concurrency,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [pp_hook],
        "logger": _LoggerAdapter(label),
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "overwrites": True,
        "windowsfilenames": False,
    }
    if opts.cookiefile:
        ydl_opts["cookiefile"] = opts.cookiefile
    runtimes = available_js_runtimes()
    if runtimes:
        ydl_opts["js_runtimes"] = runtimes
    if opts.remote_components:
        # Lets yt-dlp fetch its YouTube challenge-solver script (yt-dlp-ejs) from GitHub.
        ydl_opts["remote_components"] = {"ejs:github"}
    if opts.ffmpeg_location:
        ydl_opts["ffmpeg_location"] = opts.ffmpeg_location
    if opts.impersonate:
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget

            ydl_opts["impersonate"] = ImpersonateTarget("chrome")
        except Exception:  # noqa: BLE001
            log.debug("impersonation unavailable in yt-dlp")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except YtDlpCancelled as exc:
        raise DownloadCancelled() from exc
    except (DownloadError, UnsupportedError) as exc:
        raise classify_ytdlp_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise DownloadFailed(
            FailureKind.RETRYABLE, f"{exc.__class__.__name__}: {exc}", "ytdlp"
        ) from exc

    produced = _find_output(info, tmp_dir)
    if produced is None:
        raise DownloadFailed(
            FailureKind.RETRYABLE, "yt-dlp finished but no output file was found", "ytdlp"
        )
    entry = info
    if info and info.get("_type") == "playlist" and info.get("entries"):
        entry = next((e for e in info["entries"] if e), info)
    meta = {
        "title": (entry or {}).get("title"),
        "description": (entry or {}).get("description"),
        "upload_date": (entry or {}).get("upload_date"),
        "timestamp": (entry or {}).get("timestamp"),
    }
    return produced, meta


def _find_output(info: dict[str, Any] | None, tmp_dir: Path) -> Path | None:
    if info:
        entries = info.get("entries") if info.get("_type") == "playlist" else [info]
        for entry in entries or []:
            if not entry:
                continue
            for rd in entry.get("requested_downloads") or []:
                fp = rd.get("filepath")
                if fp and Path(fp).exists():
                    return Path(fp)
            fp = entry.get("filepath") or entry.get("_filename")
            if fp and Path(fp).exists():
                return Path(fp)
    candidates = [
        p
        for p in tmp_dir.iterdir()
        if p.is_file() and not p.name.endswith((".part", ".ytdl", ".tmp"))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


# run_ytdlp's signature; tests pass a fake.
YtDlpEngine = Callable[
    [str, Path, YtDlpOptions, ProgressReporter, str], tuple[Path, dict[str, Any]]
]


class YtDlpHandler:
    """Streams (HLS) and embedded videos (YouTube, Vimeo, ...), downloaded by yt-dlp into
    the scratch folder. yt-dlp reports an undated post's date, so the run files the result."""

    learns_date = True

    def __init__(
        self,
        settings: SettingsService,
        providers: ProviderRegistry,
        ffmpeg: Ffmpeg,
        long_work: LongWork,
        *,
        engine: YtDlpEngine = run_ytdlp,
    ) -> None:
        self._settings = settings
        self._providers = providers
        self._ffmpeg = ffmpeg
        self._long = long_work
        self._engine = engine

    async def fetch(
        self, media: MediaSnapshot, place: Placement, progress: ProgressReporter
    ) -> Fetched:
        d = self._settings.get().downloads
        provider = self._providers.get(media.provider)
        url = media.url or ""
        if media.source == MediaSource.EMBED_VIMEO:
            url = normalise_vimeo_url(url)
        opts = YtDlpOptions(
            headers=provider.media_headers(),
            cookiefile=str(provider.cookie_file) if provider.cookie_file.exists() else None,
            video_format=d.video_format,
            container=container_ext(d.container, media.provider),
            ffmpeg_location=self._ffmpeg.location,
            fragment_concurrency=d.hls_fragment_concurrency,
            impersonate=provider.ytdlp_impersonate,
            remote_components=d.ytdlp_remote_components,
        )
        progress.set_stage("downloading")
        produced, meta = await self._long.run(
            self._engine, url, place.scratch(), opts, progress, media.label
        )
        return Fetched(produced, placed=False, learned=learned_from(meta))


def learned_from(meta: dict[str, Any]) -> Learned:
    """What yt-dlp's metadata says about the post: its date (YouTube has none until now)."""
    published = None
    if meta.get("timestamp"):
        published = datetime.fromtimestamp(meta["timestamp"], tz=UTC)
    elif meta.get("upload_date"):
        try:
            published = datetime.strptime(meta["upload_date"], "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            published = None
    return Learned(published, meta.get("description") or None)

"""What a download is handed and what it reports back.

Claiming picks a queued download and hands a worker its `Claim`; the download run turns
that into an `Outcome`; the media lifecycle records the outcome. The types live here so the
lifecycle doesn't depend on the downloader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_DRM_WORD = re.compile(r"\bdrm\b", re.IGNORECASE)


@dataclass(frozen=True)
class Claim:
    """A download that has just started. The run loads everything else from these ids."""

    job_id: int
    media_id: int
    post_id: int
    #: Which try this is for the media item; a retry renews the post's links first.
    attempt: int


class FailureKind(StrEnum):
    """Why a download failed. The media lifecycle decides what follows from the kind alone."""

    DRM = "drm"
    UNSUPPORTED = "unsupported"
    NO_ACCESS = "no_access"
    FILE_GONE = "file_gone"
    POST_GONE = "post_gone"
    NEEDS_COOKIES = "needs_cookies"
    UNAVAILABLE = "unavailable"
    #: The provider's session is invalid; doesn't use up an attempt.
    AUTH = "auth"
    RETRYABLE = "retryable"

    @property
    def retried(self) -> bool:
        """Whether the media is downloaded again later. Partial files are kept for it."""
        return self in (FailureKind.AUTH, FailureKind.RETRYABLE)


@dataclass(frozen=True)
class Completed:
    #: Relative to the provider's download root, as stored on the media item.
    file_path: str
    size: int
    sha256: str | None
    metadata_embedded: bool


@dataclass(frozen=True)
class Failed:
    kind: FailureKind
    message: str
    #: A finer-grained cause shown with the download ("expired_url", "hls_probe", ...).
    label: str


@dataclass(frozen=True)
class Cancelled:
    pass


Outcome = Completed | Failed | Cancelled

INTERNAL_ERROR = Failed(FailureKind.RETRYABLE, "internal error", "internal")


def mentions_drm(message: str | None) -> bool:
    """Whether an error message blames DRM, as yt-dlp's do ("This video is DRM protected")."""
    return bool(message) and _DRM_WORD.search(message) is not None

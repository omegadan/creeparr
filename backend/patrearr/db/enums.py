"""Status enums shared by the scanner, downloader, API and UI."""

from __future__ import annotations

from enum import StrEnum


class MediaKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    ATTACHMENT = "attachment"


class MediaSource(StrEnum):
    NATIVE_DIRECT = "native_direct"
    NATIVE_HLS = "native_hls"
    EMBED_YOUTUBE = "embed_youtube"
    EMBED_VIMEO = "embed_vimeo"
    EMBED_OTHER = "embed_other"
    MEDIA_DOWNLOAD = "media_download"


class MediaStatus(StrEnum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    FAILED_PERMANENT = "failed_permanent"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"
    UNSUPPORTED_DRM = "unsupported_drm"
    NO_ACCESS = "no_access"


ACTIVE_MEDIA_STATUSES = {
    MediaStatus.DISCOVERED,
    MediaStatus.QUEUED,
    MediaStatus.DOWNLOADING,
    MediaStatus.FAILED,
}


class PostStatus(StrEnum):
    NEW = "new"
    NO_ACCESS = "no_access"
    NO_MEDIA = "no_media"
    PENDING = "pending"
    COMPLETED = "completed"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    SKIPPED = "skipped"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(StrEnum):
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    VERIFYING = "verifying"


class ScanMode(StrEnum):
    AUTO = "auto"
    FULL = "full"
    INCREMENTAL = "incremental"


class ScanStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    CREATOR_ADDED = "creator_added"
    CREATOR_REMOVED = "creator_removed"
    SCAN_STARTED = "scan_started"
    SCAN_COMPLETED = "scan_completed"
    SCAN_FAILED = "scan_failed"
    POST_DISCOVERED = "post_discovered"
    DOWNLOAD_QUEUED = "download_queued"
    DOWNLOAD_COMPLETED = "download_completed"
    DOWNLOAD_FAILED = "download_failed"
    DOWNLOAD_CANCELLED = "download_cancelled"
    MEDIA_UNSUPPORTED = "media_unsupported"
    AUTH_INVALID = "auth_invalid"
    AUTH_VALID = "auth_valid"
    QUEUE_PAUSED = "queue_paused"
    SETTINGS_CHANGED = "settings_changed"
    SYSTEM = "system"


class AuthState(StrEnum):
    UNKNOWN = "unknown"
    UNCONFIGURED = "unconfigured"
    VALID = "valid"
    INVALID = "invalid"
    CHALLENGE = "challenge"
    ERROR = "error"

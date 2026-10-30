"""Pydantic DTOs for the REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, create_model

from creeparr.core.lifecycle import ItemAction, PostAction, item_actions, post_actions
from creeparr.core.server_events import AuthStatus
from creeparr.core.settings_service import AppSettings, secret_fields
from creeparr.db.enums import (
    JobStage,
    JobStatus,
    MediaKind,
    MediaSource,
    MediaStatus,
    PostStatus,
    ScanMode,
    ScanStatus,
)
from creeparr.downloader.claims import ClaimState
from creeparr.providers.catalog import PROVIDER_TYPES


class Out(BaseModel):
    """A response body. Every field is always sent, defaults included, so the generated UI
    types mark none of them optional."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class ORMModel(Out):
    model_config = ConfigDict(from_attributes=True)


class Page[T](Out):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorBody(Out):
    code: str
    message: str
    detail: str | None = None


class ErrorResponse(Out):
    error: ErrorBody


# ---- creators -------------------------------------------------------------------------


class CreatorStats(Out):
    posts_total: int = 0
    posts_completed: int = 0
    posts_pending: int = 0
    posts_no_access: int = 0
    posts_unsupported: int = 0
    media_total: int = 0
    media_completed: int = 0
    active_jobs: int = 0
    bytes: int = 0


class CreatorOut(ORMModel):
    id: int
    provider: str
    campaign_id: str
    vanity: str | None
    name: str
    url: str | None
    avatar_url: str | None
    cover_url: str | None
    is_nsfw: bool | None
    enabled: bool
    monitored: bool
    auto_download: bool
    include_images: bool
    include_audio: bool
    include_attachments: bool
    download_since: datetime | None
    scan_interval_minutes: int | None
    folder_name: str | None
    last_scan_at: datetime | None
    last_full_scan_at: datetime | None
    last_scan_status: ScanStatus | None
    last_scan_error: str | None
    created_at: datetime
    stats: CreatorStats = Field(default_factory=CreatorStats)
    scanning: bool = False


class CreatorDefaults(BaseModel):
    enabled: bool | None = None
    monitored: bool | None = None
    auto_download: bool | None = None
    include_images: bool | None = None
    include_audio: bool | None = None
    include_attachments: bool | None = None
    download_since: datetime | None = None
    scan_interval_minutes: int | None = None


class CreatorCreate(CreatorDefaults):
    query: str = Field(min_length=1, description="URL, handle or id at the provider")
    provider: str = "patreon"


class CreatorPatch(CreatorDefaults):
    folder_name: str | None = None


class LookupRequest(BaseModel):
    query: str = Field(min_length=1)
    provider: str = "patreon"


class CreatorPreview(Out):
    provider: str
    external_id: str
    name: str
    handle: str | None
    url: str | None
    avatar_url: str | None
    description: str | None = None
    is_nsfw: bool | None = None
    already_added: bool = False
    creator_id: int | None = None


class SubscriptionOut(Out):
    provider: str
    external_id: str
    name: str
    handle: str | None
    url: str | None
    avatar_url: str | None
    is_free: bool | None = None
    is_trial: bool | None = None
    already_added: bool = False
    creator_id: int | None = None


class ImportSubscriptionsRequest(BaseModel):
    provider: str = "patreon"
    ids: list[str] = Field(min_length=1)
    defaults: CreatorDefaults = Field(default_factory=CreatorDefaults)


class CredentialFieldOut(Out):
    key: str
    label: str
    hint: str
    optional: bool
    multiline: bool


class ProviderOut(Out):
    name: str
    label: str
    intro: str
    credentials: list[CredentialFieldOut]
    post_types: dict[str, str]
    download_dir_variable: str
    configured: bool
    credential_fields: list[str]
    auth: AuthStatus


class ScanRequestBody(BaseModel):
    mode: str = "auto"


class ScanRunOut(ORMModel):
    id: int
    creator_id: int
    mode: ScanMode
    trigger: str
    status: ScanStatus
    started_at: datetime
    finished_at: datetime | None
    pages_fetched: int
    posts_seen: int
    posts_new: int
    posts_updated: int
    media_queued: int
    error: str | None


# ---- posts / media --------------------------------------------------------------------


class MediaItemOut(ORMModel):
    id: int
    post_id: int
    media_key: str
    kind: MediaKind
    source: MediaSource
    status: MediaStatus
    status_reason: str | None
    wanted: bool
    remote_file_name: str | None
    mimetype: str | None
    remote_size_bytes: int | None
    attempts: int
    next_retry_at: datetime | None
    last_error: str | None
    file_path: str | None
    file_size_bytes: int | None
    completed_at: datetime | None
    order_index: int

    @computed_field
    @property
    def actions(self) -> list[ItemAction]:
        return item_actions(self.status, self.wanted)


class MediaSummary(Out):
    total: int = 0
    completed: int = 0
    pending: int = 0
    failed: int = 0
    unsupported: int = 0
    skipped: int = 0


class PostOut(ORMModel):
    id: int
    post_id: str
    creator_id: int
    creator_name: str | None = None
    title: str
    post_type: str | None
    url: str | None
    published_at: datetime | None
    edited_at: datetime | None
    current_user_can_view: bool
    thumbnail_url: str | None
    embed_provider: str | None
    status: PostStatus
    status_reason: str | None
    folder_path: str | None
    first_seen_at: datetime
    media_summary: MediaSummary = Field(default_factory=MediaSummary)

    @computed_field
    @property
    def actions(self) -> list[PostAction]:
        return post_actions(self.status)


class PostDetailOut(PostOut):
    teaser_text: str | None = None
    media_items: list[MediaItemOut] = Field(default_factory=list)


class DownloadPostBody(BaseModel):
    force: bool = False


# ---- queue ----------------------------------------------------------------------------


class JobOut(Out):
    id: int
    media_item_id: int
    post_id: int
    creator_id: int
    creator_name: str | None
    post_title: str | None
    media_kind: MediaKind | None
    source: MediaSource | None
    file_name: str | None
    status: JobStatus
    priority: int
    attempt: int
    progress_percent: float | None
    bytes_downloaded: int | None
    total_bytes: int | None
    speed_bps: int | None
    eta_seconds: int | None
    stage: JobStage | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    error_class: str | None
    created_at: datetime


class FailedMediaOut(Out):
    media_item_id: int
    post_id: int
    creator_id: int
    creator_name: str | None
    post_title: str | None
    media_kind: MediaKind
    source: MediaSource
    status: MediaStatus
    status_reason: str | None
    attempts: int
    next_retry_at: datetime | None
    last_error: str | None


class ProviderQueueStatus(Out):
    provider: str
    label: str
    state: ClaimState
    queued: int
    running: int
    hourly_limit: int
    recent_starts: int
    next_slot_seconds: int | None = None
    next_slot_at: datetime | None = None


class QueueOut(Out):
    paused: bool
    paused_reason: str | None
    jobs: list[JobOut]
    failed: list[FailedMediaOut]
    providers: list[ProviderQueueStatus] = Field(default_factory=list)
    # Counts over the whole queue; jobs/failed above are one page of each.
    running_total: int = 0
    queued_total: int = 0
    failed_total: int = 0
    page: int = 1
    failed_page: int = 1
    page_size: int = 100


# ---- history --------------------------------------------------------------------------


class HistoryOut(Out):
    id: int
    occurred_at: datetime
    event_type: str
    level: Literal["info", "warning", "error"]
    creator_id: int | None
    creator_name: str | None
    post_id: int | None
    post_title: str | None
    media_item_id: int | None
    message: str
    data: dict[str, Any] | None


# ---- settings -------------------------------------------------------------------------


class AuthBody(BaseModel):
    """Credential fields for one provider; unknown keys are rejected by the endpoint."""

    model_config = ConfigDict(extra="allow")


class NamingPreviewBody(BaseModel):
    post_folder_template: str
    file_template: str = "{filename}"


# ---- settings, as read back ---------------------------------------------------------------


def _settings_out(model: type[BaseModel], text_secrets: set[str]) -> type[Out]:
    """A settings group as `GET /settings` sends it: the group's own fields, and for each
    text secret `has_<name>` (the secret itself reads back empty)."""
    fields: dict[str, Any] = {
        name: (info.annotation, info) for name, info in model.model_fields.items()
    }
    fields.update({f"has_{name}": (bool, False) for name in sorted(text_secrets)})
    return create_model(f"{model.__name__}Out", __base__=Out, **fields)


def _text_secrets(group: str) -> set[str]:
    return {name for name, mark in secret_fields().get(group, {}).items() if mark.text}


# Each provider's own archive root, as `<provider>_download_dir` (None = the default root).
PathsOut = create_model(
    "PathsOut",
    __base__=Out,
    config_dir=(str, ...),
    download_dir=(str, ...),
    **{f"{cls.description.name}_download_dir": (str | None, None) for cls in PROVIDER_TYPES},
)


class SettingsEnvOut(PathsOut):  # type: ignore[misc, valid-type]
    """What the container's environment sets; read-only in the UI."""

    port: int
    log_level: str
    ffmpeg: str | None


SettingsOut = create_model(
    "SettingsOut",
    __base__=Out,
    **{
        group: (_settings_out(info.annotation, _text_secrets(group)), ...)  # type: ignore[arg-type]
        for group, info in AppSettings.model_fields.items()
    },
    env=(SettingsEnvOut, ...),
)


# ---- provider accounts ------------------------------------------------------------------


class AuthUserOut(Out):
    id: str
    full_name: str | None
    vanity: str | None


class AuthTestResultOut(BaseModel):
    """Whether a provider's credentials work. Only the fields that apply are sent."""

    ok: bool
    reason: (
        Literal["not_configured", "auth_invalid", "cloudflare_challenge", "network", "unexpected"]
        | None
    ) = None
    detail: str | None = None
    user: AuthUserOut | None = None


class SetAuthOut(AuthTestResultOut):
    """The test of newly saved credentials, and the provider's settings as now stored."""

    settings: dict[str, Any]


class NotificationTestOut(BaseModel):
    ok: bool
    detail: str | None = None
    targets: list[str] | None = None


class NamingPreviewOut(Out):
    post_folder: str
    file: str
    full_path: str


# ---- UI login ---------------------------------------------------------------------------


class LoginStateOut(Out):
    auth_enabled: bool
    authenticated: bool


class OkOut(Out):
    ok: bool


# ---- system -----------------------------------------------------------------------------


class HealthOut(Out):
    status: Literal["ok", "degraded"]
    db: bool
    downloads_writable: bool
    ffmpeg: str | None
    version: str


class ScanSlotOut(Out):
    creator_id: int
    mode: ScanMode


class PendingScanOut(ScanSlotOut):
    trigger: str


class ScanQueueOut(Out):
    running: ScanSlotOut | None
    pending: list[PendingScanOut]


class DownloadsStatusOut(Out):
    paused: bool
    paused_reason: str | None
    workers: int
    running_jobs: list[int]
    free_bytes: int


class CountsOut(Out):
    creators: int
    posts: int
    media_completed: int
    media_missing: int
    media_bytes: int
    provider_bytes: dict[str, int]
    queued: int
    running: int
    failed: int


class DiskOut(Out):
    free_bytes: int | None
    total_bytes: int | None
    used_bytes: int | None


class SystemStatusOut(Out):
    version: str
    started_at: str
    uptime_seconds: int
    providers: list[ProviderOut]
    scan: ScanQueueOut
    next_scan_at: str | None
    downloads: DownloadsStatusOut
    counts: CountsOut
    disk: DiskOut
    paths: PathsOut  # type: ignore[valid-type]
    ffmpeg: str | None
    ytdlp_version: str
    http_backend: str
    curl_cffi_available: bool


class LogLineOut(Out):
    time: str
    level: str
    logger: str
    message: str


class LogsOut(Out):
    lines: list[LogLineOut]


class TaskInfoOut(Out):
    name: str
    description: str
    interval_seconds: int | None
    next_run_at: str | None
    last_run_at: str | None
    last_status: Literal["ok", "skipped", "error"] | None
    last_error: str | None
    running: bool


class TaskStartedOut(Out):
    started: bool


# ---- scans and the queue ----------------------------------------------------------------


class ScanRequestedOut(Out):
    queued: bool
    status: ScanQueueOut


class ScansQueuedOut(Out):
    queued: int


class JobRetriedOut(Out):
    job_id: int | None


class RequeuedOut(Out):
    requeued: int


class ClearedOut(Out):
    cleared: int

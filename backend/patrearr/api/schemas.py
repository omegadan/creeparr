"""Pydantic DTOs for the REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---- creators -------------------------------------------------------------------------


class CreatorStats(BaseModel):
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
    last_scan_status: str | None
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


class CreatorPreview(BaseModel):
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


class SubscriptionOut(BaseModel):
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


class ProviderOut(BaseModel):
    name: str
    label: str
    configured: bool
    credential_fields: list[str]
    auth: dict[str, Any]


class ScanRequestBody(BaseModel):
    mode: str = "auto"


class ScanRunOut(ORMModel):
    id: int
    creator_id: int
    mode: str
    trigger: str
    status: str
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
    kind: str
    source: str
    status: str
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


class MediaSummary(BaseModel):
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
    status: str
    status_reason: str | None
    folder_path: str | None
    first_seen_at: datetime
    media_summary: MediaSummary = Field(default_factory=MediaSummary)


class PostDetailOut(PostOut):
    teaser_text: str | None = None
    media_items: list[MediaItemOut] = Field(default_factory=list)


class DownloadPostBody(BaseModel):
    force: bool = False


# ---- queue ----------------------------------------------------------------------------


class JobOut(BaseModel):
    id: int
    media_item_id: int
    post_id: int
    creator_id: int
    creator_name: str | None
    post_title: str | None
    media_kind: str | None
    source: str | None
    file_name: str | None
    status: str
    priority: int
    attempt: int
    progress_percent: float | None
    bytes_downloaded: int | None
    total_bytes: int | None
    speed_bps: int | None
    eta_seconds: int | None
    stage: str | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    error_class: str | None
    created_at: datetime


class FailedMediaOut(BaseModel):
    media_item_id: int
    post_id: int
    creator_id: int
    creator_name: str | None
    post_title: str | None
    media_kind: str
    source: str
    status: str
    status_reason: str | None
    attempts: int
    next_retry_at: datetime | None
    last_error: str | None


class QueueOut(BaseModel):
    paused: bool
    paused_reason: str | None
    jobs: list[JobOut]
    failed: list[FailedMediaOut]


# ---- history --------------------------------------------------------------------------


class HistoryOut(BaseModel):
    id: int
    occurred_at: datetime
    event_type: str
    level: str
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

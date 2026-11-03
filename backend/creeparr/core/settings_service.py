"""Typed application settings persisted in the `settings` table (one row per group)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from functools import cache
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from sqlalchemy import select

from creeparr.core.errors import ValidationFailed
from creeparr.core.history import record_event
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.enums import EventType
from creeparr.db.models import Setting
from creeparr.downloader import paths

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Patreon/126.9.0.15 (Android; Android 14; Scale/2.10)"


@dataclass(frozen=True)
class Secret:
    """Marks a settings field secret: masked when settings are read, and changed only by the
    auth endpoints. A text secret (a cookies.txt) reads back as empty plus `has_<name>`."""

    text: bool = False


SecretStr = Annotated[str, Secret()]
SecretText = Annotated[str, Secret(text=True)]


class PatreonSettings(BaseModel):
    enabled: bool = True
    session_id: SecretStr = ""
    cookies_txt: SecretText = ""
    user_agent: str = DEFAULT_USER_AGENT
    requests_per_second: float = Field(default=1.0, ge=0.1, le=10)
    random_delay_min: float = Field(default=0.0, ge=0.0, le=60)
    random_delay_max: float = Field(default=0.0, ge=0.0, le=120)
    downloads_per_hour: int = Field(default=0, ge=0)
    http_backend: Literal["httpx", "curl_cffi"] = "httpx"
    impersonate_target: str = "chrome"

    @model_validator(mode="after")
    def _delay_order(self) -> PatreonSettings:
        if self.random_delay_max and self.random_delay_max < self.random_delay_min:
            self.random_delay_max = self.random_delay_min
        return self


class OnlyFansSettings(BaseModel):
    enabled: bool = True
    sess: SecretStr = ""
    auth_id: SecretStr = ""
    x_bc: SecretStr = ""
    cookies_txt: SecretText = ""
    user_agent: str = ""
    requests_per_second: float = Field(default=0.5, ge=0.1, le=5)
    random_delay_min: float = Field(default=0.0, ge=0.0, le=60)
    random_delay_max: float = Field(default=0.0, ge=0.0, le=120)
    dynamic_rules_url: str = (
        "https://raw.githubusercontent.com/DATAHOARDERS/dynamic-rules/main/onlyfans.json"
    )
    downloads_per_hour: int = Field(default=0, ge=0)
    include_archived: bool = True
    include_messages: bool = True
    http_backend: Literal["httpx", "curl_cffi"] = "httpx"
    impersonate_target: str = "chrome"

    @model_validator(mode="after")
    def _delay_order(self) -> OnlyFansSettings:
        if self.random_delay_max and self.random_delay_max < self.random_delay_min:
            self.random_delay_max = self.random_delay_min
        return self


class RedditSettings(BaseModel):
    enabled: bool = True
    client_id: str = ""
    client_secret: SecretStr = ""
    user_agent: str = ""
    max_posts: int = Field(default=0, ge=0)
    sleep_request: float = Field(default=0.6, ge=0.0, le=30)
    downloads_per_hour: int = Field(default=0, ge=0)


class InstagramSettings(BaseModel):
    enabled: bool = True
    sessionid: SecretStr = ""
    cookies_txt: SecretText = ""
    user_agent: str = ""
    include_reels: bool = True
    include_stories: bool = False
    include_highlights: bool = False
    include_tagged: bool = False
    max_posts: int = Field(default=0, ge=0)
    sleep_request: float = Field(default=1.0, ge=0.0, le=30)
    downloads_per_hour: int = Field(default=0, ge=0)


class YouTubeSettings(BaseModel):
    enabled: bool = True
    cookies_txt: SecretText = ""
    max_videos: int = Field(default=0, ge=0)
    downloads_per_hour: int = Field(default=0, ge=0)


class ScanSettings(BaseModel):
    interval_minutes: int = Field(default=60, ge=0, le=10080)
    overlap_posts: int = Field(default=10, ge=1, le=500)
    full_rescan_days: int = Field(default=0, ge=0, le=365)
    default_auto_download: bool = True
    default_include_images: bool = False
    default_include_audio: bool = False
    default_include_attachments: bool = False


class DownloadSettings(BaseModel):
    concurrency: int = Field(default=2, ge=1, le=8)
    max_per_creator: int = Field(default=2, ge=1, le=8)
    min_free_mb: int = Field(default=1024, ge=0)
    max_attempts: int = Field(default=5, ge=1, le=50)
    retry_base_seconds: int = Field(default=60, ge=5)
    retry_cap_seconds: int = Field(default=21600, ge=60)
    url_max_age_minutes: int = Field(default=30, ge=1)
    hls_fragment_concurrency: int = Field(default=4, ge=1, le=16)
    compute_sha256: bool = True
    deduplicate: bool = True
    # Archive integrity: how often to check that archived files still exist
    # (0 = only when run manually), and whether missing ones are re-queued.
    verify_files_hours: int = Field(default=0, ge=0, le=720)
    requeue_missing: bool = False
    # With a per-provider downloads_per_hour cap, space starts out at random
    # intervals across the hour instead of bursting up to the cap.
    spread_downloads: bool = True
    video_format: str = "bv*+ba/b"
    container: Literal["auto", "mp4", "mkv"] = "auto"
    ytdlp_remote_components: bool = True


class NamingSettings(BaseModel):
    post_folder_template: str = "{creator}/{published:%Y-%m-%d} - {title} [{post_id}]"
    file_template: str = "{filename}"
    max_component_length: int = Field(default=150, ge=20, le=240)
    write_sidecars: bool = True
    write_nfo: bool = False
    embed_metadata: bool = True

    @field_validator("post_folder_template", "file_template")
    @classmethod
    def _renders(cls, template: str, info: ValidationInfo) -> str:
        # The path rules' own check, so a template no download could use is never saved.
        problem = paths.template_problem(template, file=info.field_name == "file_template")
        if problem:
            raise ValueError(problem)
        return template


class SecuritySettings(BaseModel):
    auth_enabled: bool = False
    password_hash: SecretStr = ""
    # Part of the session signing key: logging out bumps it, ending every session.
    session_epoch: int = 0
    # The first-run question (set a password, or go on without one) has been answered.
    setup_done: bool = False


class NotificationSettings(BaseModel):
    webhook_url: str = ""
    discord_webhook: str = ""
    ntfy_url: str = ""
    notify_auth_invalid: bool = True
    notify_scan_failed: bool = True
    notify_download_failed: bool = False
    notify_scan_completed: bool = False


class HistorySettings(BaseModel):
    retention_days: int = Field(default=90, ge=1)
    job_retention_days: int = Field(default=30, ge=1)


class AppSettings(BaseModel):
    patreon: PatreonSettings = Field(default_factory=PatreonSettings)
    onlyfans: OnlyFansSettings = Field(default_factory=OnlyFansSettings)
    youtube: YouTubeSettings = Field(default_factory=YouTubeSettings)
    instagram: InstagramSettings = Field(default_factory=InstagramSettings)
    reddit: RedditSettings = Field(default_factory=RedditSettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    downloads: DownloadSettings = Field(default_factory=DownloadSettings)
    naming: NamingSettings = Field(default_factory=NamingSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    history: HistorySettings = Field(default_factory=HistorySettings)


@cache
def secret_fields() -> dict[str, dict[str, Secret]]:
    """Each settings group's secret fields, as marked on its model."""
    found: dict[str, dict[str, Secret]] = {}
    for group, info in AppSettings.model_fields.items():
        model = info.annotation
        for name, field_info in model.model_fields.items():  # type: ignore[union-attr]
            mark = next((m for m in field_info.metadata if isinstance(m, Secret)), None)
            if mark is not None:
                found.setdefault(group, {})[name] = mark
    return found


def mask_secret(value: str) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) > 8 else ""
    return f"••••{tail}"


def reasons(exc: ValidationError) -> str:
    """What's wrong, readably: `naming.file_template: The file name template can't ...`."""
    return "; ".join(
        f"{'.'.join(map(str, e['loc']))}: {e['msg'].removeprefix('Value error, ')}"
        for e in exc.errors()
    )


def _usable(model: type[BaseModel], value: Any) -> tuple[dict[str, Any], list[str]]:
    """A stored group's settings, with each one that fails validation reset to its default,
    and the names of those reset."""
    if not isinstance(value, dict):
        return model().model_dump(), sorted(model.model_fields)
    value = dict(value)
    dropped: list[str] = []
    while True:
        try:
            return model.model_validate(value).model_dump(), sorted(dropped)
        except ValidationError as exc:
            bad = {e["loc"][0] for e in exc.errors() if e["loc"] and e["loc"][0] in value}
            if not bad:  # not one setting's fault: reset the group
                return model().model_dump(), sorted({*dropped, *value})
            dropped.extend(str(name) for name in bad)
            for name in bad:
                del value[name]


class SettingsService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._factory = session_factory
        # Re-entrant: update() holds it across get() (which may reload) and the write.
        self._lock = threading.RLock()
        self._cache: AppSettings | None = None

    def reload(self) -> AppSettings:
        with self._lock:
            with session_scope(self._factory) as s:
                rows = s.execute(select(Setting)).scalars().all()
                data = {r.key: r.value or {} for r in rows}
            try:
                self._cache = AppSettings.model_validate(data)
            except ValidationError as exc:
                log.warning(
                    "Stored settings failed validation; resetting what can't be used: %s", exc
                )
                merged: dict[str, Any] = {}
                reset: dict[str, list[str]] = {}
                for group, value in data.items():
                    if group not in AppSettings.model_fields:
                        continue
                    model = AppSettings.model_fields[group].annotation
                    merged[group], dropped = _usable(model, value)  # type: ignore[arg-type]
                    if dropped:
                        reset[group] = dropped
                self._cache = AppSettings.model_validate(merged)
                self._save_reset(reset)
            return self._cache

    def _save_reset(self, reset: dict[str, list[str]]) -> None:
        """Store the groups that had settings reset, so the reset happens once, and say so in
        the history."""
        if not reset:
            return
        assert self._cache is not None
        with session_scope(self._factory) as s:
            for group, names in reset.items():
                row = s.get(Setting, group)
                if row is not None:
                    row.value = getattr(self._cache, group).model_dump()
                for name in names:
                    message = f"{group}.{name} couldn't be used and was reset to the default"
                    log.warning(message)
                    record_event(s, None, EventType.SETTINGS_CHANGED, message, level="warning")

    def get(self) -> AppSettings:
        if self._cache is None:
            return self.reload()
        return self._cache

    def update(self, patch: dict[str, Any], *, allow_secrets: bool = False) -> AppSettings:
        """Deep-merge `patch` (group -> fields) into current settings and persist changed groups."""
        # One lock around read, merge and write: two concurrent updates to different
        # fields of a group must not each write back the other's stale copy.
        with self._lock:
            current = self.get().model_dump()
            secrets = secret_fields()
            for group, fields in patch.items():
                if group not in AppSettings.model_fields:
                    raise ValidationFailed(f"unknown settings group '{group}'")
                if not isinstance(fields, dict):
                    raise ValidationFailed(f"settings group '{group}' must be an object")
                for name, value in fields.items():
                    if name not in current[group]:
                        raise ValidationFailed(f"unknown setting '{group}.{name}'")
                    if not allow_secrets and name in secrets.get(group, {}):
                        continue
                    current[group][name] = value
            try:
                validated = AppSettings.model_validate(current)
            except ValidationError as exc:
                raise ValidationFailed("invalid settings", detail=reasons(exc)) from exc
            with session_scope(self._factory) as s:
                for group in patch:
                    row = s.get(Setting, group)
                    value = getattr(validated, group).model_dump()
                    if row is None:
                        s.add(Setting(key=group, value=value))
                    else:
                        row.value = value
            self._cache = validated
        return validated

    def masked(self) -> dict[str, Any]:
        data = self.get().model_dump()
        for group, marks in secret_fields().items():
            for name, mark in marks.items():
                value = data[group].get(name, "")
                if mark.text:
                    data[group][f"has_{name}"] = bool(value)
                    data[group][name] = ""
                else:
                    data[group][name] = mask_secret(value)
        return data

"""Typed application settings persisted in the `settings` table (one row per group)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from patreonarr.core.errors import ValidationFailed
from patreonarr.db.engine import SessionFactory, session_scope
from patreonarr.db.models import Setting

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Patreon/126.9.0.15 (Android; Android 14; Scale/2.10)"


class PatreonSettings(BaseModel):
    session_id: str = ""
    cookies_txt: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    requests_per_second: float = Field(default=1.0, ge=0.1, le=10)
    http_backend: Literal["httpx", "curl_cffi"] = "httpx"
    impersonate_target: str = "chrome"


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
    video_format: str = "bv*+ba/b"
    ytdlp_remote_components: bool = True


class NamingSettings(BaseModel):
    post_folder_template: str = "{creator}/{published:%Y-%m-%d} - {title} [{post_id}]"
    file_template: str = "{filename}"
    max_component_length: int = Field(default=150, ge=20, le=240)
    write_sidecars: bool = True


class HistorySettings(BaseModel):
    retention_days: int = Field(default=90, ge=1)
    job_retention_days: int = Field(default=30, ge=1)


class AppSettings(BaseModel):
    patreon: PatreonSettings = Field(default_factory=PatreonSettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    downloads: DownloadSettings = Field(default_factory=DownloadSettings)
    naming: NamingSettings = Field(default_factory=NamingSettings)
    history: HistorySettings = Field(default_factory=HistorySettings)


SECRET_FIELDS: dict[str, set[str]] = {"patreon": {"session_id", "cookies_txt"}}


def mask_secret(value: str) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) > 8 else ""
    return f"••••{tail}"


class SettingsService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._factory = session_factory
        self._lock = threading.Lock()
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
                    "Stored settings failed validation; using defaults where needed: %s", exc
                )
                merged: dict[str, Any] = {}
                for group, value in data.items():
                    if group not in AppSettings.model_fields:
                        continue
                    model = AppSettings.model_fields[group].annotation
                    try:
                        merged[group] = model.model_validate(value).model_dump()  # type: ignore[union-attr]
                    except ValidationError:
                        merged[group] = {}
                self._cache = AppSettings.model_validate(merged)
            return self._cache

    def get(self) -> AppSettings:
        if self._cache is None:
            return self.reload()
        return self._cache

    def update(self, patch: dict[str, Any], *, allow_secrets: bool = False) -> AppSettings:
        """Deep-merge `patch` (group -> fields) into current settings and persist changed groups."""
        current = self.get().model_dump()
        for group, fields in patch.items():
            if group not in AppSettings.model_fields:
                raise ValidationFailed(f"unknown settings group '{group}'")
            if not isinstance(fields, dict):
                raise ValidationFailed(f"settings group '{group}' must be an object")
            for name, value in fields.items():
                if name not in current[group]:
                    raise ValidationFailed(f"unknown setting '{group}.{name}'")
                if not allow_secrets and name in SECRET_FIELDS.get(group, set()):
                    continue
                current[group][name] = value
        try:
            validated = AppSettings.model_validate(current)
        except ValidationError as exc:
            raise ValidationFailed("invalid settings", detail=str(exc)) from exc

        with self._lock:
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
        data["patreon"]["session_id"] = mask_secret(data["patreon"]["session_id"])
        data["patreon"]["has_cookies_txt"] = bool(data["patreon"]["cookies_txt"])
        data["patreon"]["cookies_txt"] = ""
        return data

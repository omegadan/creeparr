"""ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from patrearr.db.enums import (
    JobStatus,
    MediaKind,
    MediaSource,
    MediaStatus,
    PostStatus,
    ScanMode,
    ScanStatus,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store aware datetimes as naive UTC in SQLite, return them aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Creator(TimestampMixin, Base):
    __tablename__ = "creators"
    __table_args__ = (UniqueConstraint("provider", "campaign_id", name="uq_creator_provider_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="patreon", nullable=False)
    #: The provider's own id for this creator (Patreon campaign id, OnlyFans user id, ...).
    campaign_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vanity: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024))
    creator_user_id: Mapped[str | None] = mapped_column(String(32))
    creation_name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    cover_url: Mapped[str | None] = mapped_column(String(2048))
    is_nsfw: Mapped[bool | None] = mapped_column(Boolean)

    monitored: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_download: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    include_images: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    include_audio: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    include_attachments: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    download_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    folder_name: Mapped[str | None] = mapped_column(String(255))

    last_scan_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_full_scan_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_scan_status: Mapped[str | None] = mapped_column(String(16))
    last_scan_error: Mapped[str | None] = mapped_column(Text)
    pledge_active: Mapped[bool | None] = mapped_column(Boolean)
    pledge_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    posts: Mapped[list[Post]] = relationship(
        back_populates="creator", cascade="all, delete-orphan", passive_deletes=True
    )


class Post(TimestampMixin, Base):
    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_creator_published", "creator_id", "published_at"),
        UniqueConstraint("creator_id", "post_id", name="uq_post_creator_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    creator_id: Mapped[int] = mapped_column(
        ForeignKey("creators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    content_html: Mapped[str | None] = mapped_column(Text)
    teaser_text: Mapped[str | None] = mapped_column(Text)
    post_type: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(2048))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    current_user_can_view: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    embed_provider: Mapped[str | None] = mapped_column(String(64))
    embed_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(
        String(32), default=PostStatus.NEW, nullable=False, index=True
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    folder_path: Mapped[str | None] = mapped_column(String(2048))
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    urls_fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sidecars_written: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    creator: Mapped[Creator] = relationship(back_populates="posts")
    media_items: Mapped[list[MediaItem]] = relationship(
        back_populates="post",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MediaItem.id",
    )


class MediaItem(TimestampMixin, Base):
    __tablename__ = "media_items"
    __table_args__ = (
        UniqueConstraint("post_id", "media_key", name="uq_media_post_key"),
        Index("ix_media_status", "status"),
        Index("ix_media_retry", "status", "next_retry_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    creator_id: Mapped[int] = mapped_column(
        ForeignKey("creators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    media_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=MediaKind.VIDEO, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), default=MediaSource.NATIVE_DIRECT, nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    remote_file_name: Mapped[str | None] = mapped_column(String(1024))
    mimetype: Mapped[str | None] = mapped_column(String(128))
    remote_size_bytes: Mapped[int | None] = mapped_column(Integer)
    remote_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default=MediaStatus.DISCOVERED, nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text)
    wanted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(2048))
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    post: Mapped[Post] = relationship(back_populates="media_items")
    jobs: Mapped[list[DownloadJob]] = relationship(
        back_populates="media_item", cascade="all, delete-orphan", passive_deletes=True
    )


class DownloadJob(TimestampMixin, Base):
    __tablename__ = "download_jobs"
    __table_args__ = (
        Index(
            "uq_job_active_media",
            "media_item_id",
            unique=True,
            sqlite_where=text("status IN ('queued','running')"),
        ),
        Index("ix_jobs_status_priority", "status", "priority", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_item_id: Mapped[int] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    creator_id: Mapped[int] = mapped_column(
        ForeignKey("creators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    progress_percent: Mapped[float | None] = mapped_column(Float)
    bytes_downloaded: Mapped[int | None] = mapped_column(Integer)
    total_bytes: Mapped[int | None] = mapped_column(Integer)
    speed_bps: Mapped[int | None] = mapped_column(Integer)
    eta_seconds: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str | None] = mapped_column(String(16))
    worker_id: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)
    error_class: Mapped[str | None] = mapped_column(String(64))

    media_item: Mapped[MediaItem] = relationship(back_populates="jobs")


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(
        ForeignKey("creators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(16), default=ScanMode.INCREMENTAL, nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ScanStatus.RUNNING, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    posts_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    posts_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    posts_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    media_queued: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class History(Base):
    __tablename__ = "history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(8), default="info", nullable=False)
    creator_id: Mapped[int | None] = mapped_column(
        ForeignKey("creators.id", ondelete="SET NULL"), index=True
    )
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id", ondelete="SET NULL"))
    media_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_items.id", ondelete="SET NULL")
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class SystemState(Base):
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

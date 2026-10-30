"""Every server-sent event, by name, with its payload. The UI's handlers are typed from these
(see creeparr.api.contract), so a handler can't read a field that's never sent."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel

from creeparr.db.enums import AuthState, JobStage, JobStatus, PostStatus, ScanMode, ScanStatus

# Each event's name and payload model, in the order they're declared.
EVENTS: dict[str, type[ServerEvent]] = {}


class ServerEvent(BaseModel):
    event: ClassVar[str]

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        name = cls.__dict__.get("event")
        if name is None:
            return  # a shared shape, not an event of its own
        if name in EVENTS:
            raise ValueError(f"two events are named {name}")
        EVENTS[name] = cls

    def payload(self) -> dict[str, Any]:
        # Only what was set, as before: `job.progress` merges into the cached job, so an
        # unset field must stay out rather than overwrite it with null.
        return self.model_dump(mode="json", exclude_unset=True)


class AuthStatus(BaseModel):
    """A provider's session, as last checked."""

    provider: str
    state: AuthState
    checked_at: str | None = None
    user_name: str | None = None
    user_id: str | None = None
    error: str | None = None


class HelloEvent(ServerEvent):
    """Sent first on every connection, so a reconnect tells the UI which server it reached."""

    event = "hello"
    version: str
    providers: dict[str, AuthStatus]


class AuthStatusEvent(AuthStatus, ServerEvent):
    event = "auth.status"


class ScanQueuedEvent(ServerEvent):
    event = "scan.queued"
    creator_id: int
    mode: ScanMode


class ScanStartedEvent(ServerEvent):
    event = "scan.started"
    scan_run_id: int
    creator_id: int
    mode: ScanMode


class ScanProgressEvent(ServerEvent):
    event = "scan.progress"
    scan_run_id: int
    creator_id: int
    seen: int
    new: int


class ScanFinishedEvent(ServerEvent):
    event = "scan.finished"
    scan_run_id: int
    creator_id: int
    status: ScanStatus
    error: str | None = None


class JobProgressEvent(ServerEvent):
    """A running download's progress; only the fields that are known are sent."""

    event = "job.progress"
    id: int
    progress_percent: float | None = None
    bytes_downloaded: int | None = None
    total_bytes: int | None = None
    speed_bps: int | None = None
    eta_seconds: int | None = None
    stage: JobStage | None = None


class JobStartedEvent(ServerEvent):
    event = "job.started"
    id: int
    media_item_id: int
    post_id: int


class JobFinishedEvent(ServerEvent):
    event = "job.finished"
    id: int
    media_item_id: int
    post_id: int
    status: JobStatus
    error: str | None = None


class QueueChangedEvent(ServerEvent):
    event = "queue.changed"


class QueuePausedEvent(ServerEvent):
    event = "queue.paused"
    reason: str


class QueueResumedEvent(ServerEvent):
    event = "queue.resumed"


class HistoryAddedEvent(ServerEvent):
    event = "history.added"
    id: int
    event_type: str
    level: str
    message: str
    creator_id: int | None = None
    post_id: int | None = None


class CreatorChangedEvent(ServerEvent):
    event = "creator.changed"
    id: int
    deleted: bool = False


class PostChangedEvent(ServerEvent):
    event = "post.changed"
    id: int
    creator_id: int
    status: PostStatus


class SettingsChangedEvent(ServerEvent):
    event = "settings.changed"
    groups: list[str]

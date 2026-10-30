"""History table helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from creeparr.core.events import EventBus
from creeparr.core.server_events import HistoryAddedEvent
from creeparr.db.enums import EventType
from creeparr.db.models import History


def record_event(
    session: Session,
    bus: EventBus | None,
    event_type: EventType | str,
    message: str,
    *,
    level: str = "info",
    creator_id: int | None = None,
    post_id: int | None = None,
    media_item_id: int | None = None,
    data: dict[str, Any] | None = None,
) -> History:
    row = History(
        event_type=str(event_type),
        level=level,
        creator_id=creator_id,
        post_id=post_id,
        media_item_id=media_item_id,
        message=message,
        data=data,
    )
    session.add(row)
    session.flush()
    if bus is not None:
        bus.publish(added_event(row))
    return row


def added_event(row: History) -> HistoryAddedEvent:
    """The `history.added` event for a new row."""
    return HistoryAddedEvent(
        id=row.id,
        event_type=row.event_type,
        level=row.level,
        message=row.message,
        creator_id=row.creator_id,
        post_id=row.post_id,
    )

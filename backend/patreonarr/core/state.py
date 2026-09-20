"""Runtime key/value state (auth status, paused flags) stored in `system_state`."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from patreonarr.db.models import SystemState


def get_state(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(SystemState, key)
    return row.value if row is not None and row.value is not None else default


def set_state(session: Session, key: str, value: Any) -> None:
    row = session.get(SystemState, key)
    if row is None:
        session.add(SystemState(key=key, value=value))
    else:
        row.value = value

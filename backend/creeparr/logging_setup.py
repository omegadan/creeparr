"""Logging: stdout, rotating file, and an in-memory ring buffer for the UI."""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_SECRET_RE = re.compile(r"(session_id=)([^;\s\"']+)", re.IGNORECASE)


class RedactFilter(logging.Filter):
    """Mask session cookies wherever they might sneak into a log line."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = _SECRET_RE.sub(r"\1***", msg)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self._buf: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record) if self.formatter else record.getMessage(),
            }
        except Exception:  # pragma: no cover - never break the app for logging
            return
        with self._lock:
            self._buf.append(entry)

    def tail(
        self, lines: int = 500, level: str | None = None, search: str | None = None
    ) -> list[dict]:
        with self._lock:
            items = list(self._buf)
        if level:
            min_level = logging.getLevelName(level.upper())
            if isinstance(min_level, int):
                items = [i for i in items if logging.getLevelName(i["level"]) >= min_level]
        if search:
            s = search.lower()
            items = [i for i in items if s in i["message"].lower()]
        return items[-lines:]


ring_buffer = RingBufferHandler()


def setup_logging(level: str, log_dir: Path | None) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(FORMAT)
    redact = RedactFilter()

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.addFilter(redact)
    root.addHandler(stream)

    ring_buffer.setFormatter(logging.Formatter("%(message)s"))
    ring_buffer.addFilter(redact)
    root.addHandler(ring_buffer)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_dir / "creeparr.log", maxBytes=10 * 1024 * 1024, backupCount=5)
        fh.setFormatter(fmt)
        fh.addFilter(redact)
        root.addHandler(fh)

    # Quieten noisy libraries.
    for name in ("httpx", "httpcore", "apscheduler", "uvicorn.access", "hpack"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(level)

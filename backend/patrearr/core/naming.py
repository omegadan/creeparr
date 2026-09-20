"""Path templates and filename sanitising."""

from __future__ import annotations

import re
import string
import unicodedata
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}  # fmt: skip
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WS = re.compile(r"\s+")


def sanitize_component(value: Any, max_len: int = 150) -> str:
    s = unicodedata.normalize("NFC", str(value or ""))
    s = _WS.sub(" ", s).strip()
    s = _ILLEGAL.sub("_", s)
    s = s.strip(". ")
    if not s:
        return "untitled"
    if s.upper().split(".")[0] in _WINDOWS_RESERVED:
        s = f"{s}_"
    # Truncate on a character boundary to at most max_len bytes.
    encoded = s.encode("utf-8")
    if len(encoded) > max_len:
        s = encoded[:max_len].decode("utf-8", errors="ignore").rstrip(". ")
        if not s:
            s = "untitled"
    return s


class SafeFormatter(string.Formatter):
    """Formats a template, sanitising every substituted value."""

    def __init__(self, max_len: int = 150) -> None:
        super().__init__()
        self.max_len = max_len
        self.missing: list[str] = []

    def get_value(self, key, args, kwargs):  # noqa: ANN001
        if isinstance(key, str):
            if key in kwargs:
                return kwargs[key]
            self.missing.append(key)
            return ""
        return super().get_value(key, args, kwargs)

    def format_field(self, value, format_spec):  # noqa: ANN001
        if isinstance(value, datetime):
            text = value.strftime(format_spec or "%Y-%m-%d")
        elif format_spec:
            try:
                text = format(value, format_spec)
            except (ValueError, TypeError):
                text = str(value)
        else:
            text = "" if value is None else str(value)
        return sanitize_component(text, self.max_len) if text != "" else ""


def render_template(template: str, values: dict[str, Any], max_len: int = 150) -> PurePosixPath:
    """Render to a relative path. Slashes in the template separate segments; values never do."""
    fmt = SafeFormatter(max_len)
    rendered = fmt.vformat(template, (), values)
    raw_parts = [p.strip() for p in rendered.replace("\\", "/").split("/")]
    parts = [sanitize_component(p, max_len) for p in raw_parts if p and p not in (".", "..")]
    if not parts:
        parts = ["untitled"]
    return PurePosixPath(*parts)


def split_name(file_name: str | None) -> tuple[str, str]:
    """('name', 'ext') where ext has no leading dot and may be empty."""
    if not file_name:
        return "", ""
    p = Path(file_name)
    ext = p.suffix.lstrip(".").lower()
    return p.stem, ext


def unique_path(path: Path) -> Path:
    """Return `path` if free, else `name (2).ext`, `name (3).ext`, ..."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while True:
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1

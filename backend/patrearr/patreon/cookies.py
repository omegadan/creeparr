"""Cookie handling: a pasted session_id, an optional cookies.txt, and export for yt-dlp."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

PATREON_DOMAIN = "patreon.com"


@dataclass
class NetscapeCookie:
    domain: str
    include_subdomains: bool
    path: str
    secure: bool
    expires: int
    name: str
    value: str


def parse_netscape(text: str) -> list[NetscapeCookie]:
    cookies: list[NetscapeCookie] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        http_only = False
        if line.startswith("#HttpOnly_"):
            http_only = True
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = line.split()
            if len(parts) < 7:
                continue
        domain, flag, path, secure, expires, name = parts[:6]
        value = "\t".join(parts[6:]) if "\t" in line else parts[6]
        try:
            exp = int(float(expires))
        except ValueError:
            exp = 0
        cookies.append(
            NetscapeCookie(
                domain=domain if not http_only else domain,
                include_subdomains=flag.upper() == "TRUE",
                path=path or "/",
                secure=secure.upper() == "TRUE",
                expires=exp,
                name=name,
                value=value,
            )
        )
    return cookies


@dataclass
class CookieSet:
    session_id: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
    domain: str = PATREON_DOMAIN

    @classmethod
    def from_settings(
        cls,
        session_id: str | None,
        cookies_txt: str | None,
        *,
        domain: str = PATREON_DOMAIN,
        session_name: str = "session_id",
    ) -> CookieSet:
        extra: dict[str, str] = {}
        sid = (session_id or "").strip() or None
        if cookies_txt:
            for c in parse_netscape(cookies_txt):
                if domain not in c.domain:
                    continue
                if c.name == session_name:
                    if not sid:
                        sid = c.value
                    continue
                extra[c.name] = c.value
        return cls(session_id=sid, extra=extra, domain=domain)

    @property
    def is_configured(self) -> bool:
        return bool(self.session_id)

    def as_dict(self) -> dict[str, str]:
        d = dict(self.extra)
        if self.session_id:
            d["session_id"] = self.session_id
        return d

    def header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.as_dict().items())

    def write_netscape(self, path: Path) -> None:
        """Write a cookies.txt usable by yt-dlp/ffmpeg. Chmod 600."""
        netscape_domain = self.domain if self.domain.startswith(".") else f".{self.domain}"
        expires = int(time.time()) + 365 * 24 * 3600
        lines = ["# Netscape HTTP Cookie File", "# Written by patrearr", ""]
        for name, value in self.as_dict().items():
            lines.append(f"{netscape_domain}\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}")
        _atomic_write(path, "\n".join(lines) + "\n")


def write_cookiefile(text: str, path: Path) -> int:
    """Write a full, pasted cookies.txt verbatim for yt-dlp/gallery-dl (any domain).

    Every cookie keeps its own domain, so this works for YouTube, Instagram, etc.,
    unlike ``CookieSet.write_netscape`` which stamps a single domain. Space-separated
    exports are normalised to tabs. Returns the number of cookies written.
    """
    cookies = parse_netscape(text)
    if not cookies:
        return 0
    expires_default = int(time.time()) + 365 * 24 * 3600
    lines = ["# Netscape HTTP Cookie File", "# Written by patrearr", ""]
    for c in cookies:
        lines.append(
            "\t".join(
                (
                    c.domain,
                    "TRUE" if c.include_subdomains else "FALSE",
                    c.path or "/",
                    "TRUE" if c.secure else "FALSE",
                    str(c.expires or expires_default),
                    c.name,
                    c.value,
                )
            )
        )
    _atomic_write(path, "\n".join(lines) + "\n")
    return len(cookies)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

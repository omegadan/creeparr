"""Request checks that stop other websites from driving the API through a browser.

Two attacks matter for a self-hosted app on the LAN:

* Cross-site requests (CSRF). Any page the user visits can send a bodyless POST to
  ``http://<lan-ip>:7979/api/v1/...``. State-changing API calls must therefore carry
  a custom header; a browser only lets another origin send one after a CORS
  preflight, which this app never approves.
* DNS rebinding. A page on ``evil.example`` re-points its DNS at the LAN address and
  becomes same-origin, so the header check no longer helps. While no password is set
  we only accept Host names that can't be rebound from the internet: IP literals,
  bare hostnames, local-only suffixes, and anything in ``CREEPARR_ALLOWED_HOSTS``.
  With a password set the attacker has no session cookie, so every gated call fails.
"""

from __future__ import annotations

import ipaddress

CSRF_HEADER = "X-Requested-With"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Suffixes that only resolve on a local network or a private overlay (Tailscale).
LOCAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".lan",
    ".home",
    ".home.arpa",
    ".internal",
    ".localdomain",
    ".ts.net",
)


def host_name(host_header: str) -> str:
    """The name part of a Host header: no port, no IPv6 brackets, lowercase."""
    h = host_header.strip().lower()
    if h.startswith("["):  # [::1]:7979
        return h[1 : h.find("]")] if "]" in h else h[1:]
    if h.count(":") == 1:
        h = h.split(":", 1)[0]
    return h.rstrip(".")


def parse_allowed_hosts(value: str) -> tuple[str, ...]:
    """``CREEPARR_ALLOWED_HOSTS``: comma-separated names; ``.example.com`` matches
    subdomains; ``*`` turns the Host check off."""
    return tuple(h.strip().lower() for h in value.split(",") if h.strip())


def is_allowed_host(host_header: str | None, extra: tuple[str, ...] = ()) -> bool:
    if "*" in extra:
        return True
    name = host_name(host_header or "")
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True  # an IP literal can't be rebound
    except ValueError:
        pass
    if name == "localhost" or "." not in name or name.endswith(LOCAL_SUFFIXES):
        return True
    for allowed in extra:
        if allowed.startswith("."):
            if name.endswith(allowed) or name == allowed[1:]:
                return True
        elif name == host_name(allowed):
            return True
    return False


def needs_csrf_header(method: str, path: str) -> bool:
    return method.upper() not in SAFE_METHODS and path.startswith("/api/")

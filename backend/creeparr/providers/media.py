"""How a provider fetches media files: its media session, and the one stream that every
download and cover goes through."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from creeparr.providers.errors import TransportFailure, TransportUnavailable
from creeparr.providers.http import (
    RateLimiter,
    Transport,
    TransportResponse,
    build_transport,
    host_matches,
    with_range,
)

log = logging.getLogger(__name__)

_RETRY_DELAY = 2.0


class MediaRef(Protocol):
    """What a provider is told about a stored media item. The download's snapshot fits."""

    @property
    def source(self) -> str: ...
    @property
    def post_type(self) -> str | None: ...
    @property
    def remote_metadata(self) -> Mapping[str, Any]: ...
    @property
    def url(self) -> str | None: ...


@dataclass(frozen=True)
class MediaSession:
    """How a provider fetches files as its settings are now. Built for each use, never kept."""

    #: Sent with every request, and handed to yt-dlp.
    headers: Mapping[str, str] = field(default_factory=dict)
    #: The session's cookie header: sent only to `cookie_domain` and its subdomains.
    cookie: str = ""
    cookie_domain: str = ""
    #: The cookies.txt yt-dlp reads, when one exists.
    cookie_file: Path | None = None
    #: The browser curl_cffi impersonates; None fetches with httpx.
    impersonate: str | None = None
    #: Paces file requests together with the provider's API requests.
    limiter: RateLimiter | None = None

    async def stream(self, url: str, *, range_start: int = 0) -> TransportResponse:
        """Open a streaming GET of `url`, from byte `range_start` on.

        The status isn't checked: the caller maps it, and closes the response. Each call has
        its own connection, closed with the response, so a settings change never closes one
        in use. A dropped connection is retried once; a second drop raises TransportFailure.
        """
        headers = with_range(dict(self.headers), range_start)
        if self.cookie and self.cookie_domain and host_matches(url, self.cookie_domain):
            headers["Cookie"] = self.cookie
        try:
            return await self._open(url, headers)
        except TransportFailure as exc:
            log.warning("GET %s failed (%s); retrying", url, exc)
            await asyncio.sleep(_RETRY_DELAY)
        return await self._open(url, headers)

    async def _open(self, url: str, headers: dict[str, str]) -> TransportResponse:
        if self.limiter is not None:
            await self.limiter.wait()
        transport = _transport(self.impersonate)
        try:
            resp = await transport.request("GET", url, headers=headers, stream=True)
        except TransportFailure:
            await transport.aclose()
            raise
        resp.also_close(transport.aclose)
        return resp


def _transport(impersonate: str | None) -> Transport:
    if impersonate is None:
        return build_transport("httpx")
    try:
        return build_transport("curl_cffi", impersonate)
    except TransportUnavailable as exc:
        log.warning("%s; falling back to httpx", exc)
        return build_transport("httpx")

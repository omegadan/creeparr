"""HTTP transport abstraction so the client can swap httpx for curl_cffi."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from creeparr.patreon.errors import TransportFailure, TransportUnavailable

ByteStream = Callable[..., AsyncIterator[bytes]]


class TransportResponse:
    """Backend-agnostic response wrapper."""

    def __init__(
        self,
        status: int,
        headers: dict[str, str],
        *,
        url: str = "",
        content: bytes | None = None,
        stream: ByteStream | None = None,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.url = url
        self._content = content
        self._stream = stream
        self._close = close

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    @property
    def content(self) -> bytes:
        return self._content or b""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    async def aiter_bytes(self, chunk_size: int = 1 << 16) -> AsyncIterator[bytes]:
        if self._stream is None:
            if self._content:
                yield self._content
            return
        try:
            async for chunk in self._stream(chunk_size):
                yield chunk
        except Exception as exc:  # noqa: BLE001 - normalise any backend error
            raise TransportFailure(f"stream failed: {exc}") from exc

    async def aclose(self) -> None:
        if self._close is not None:
            await self._close()


class Transport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        stream: bool = False,
    ) -> TransportResponse: ...

    async def aclose(self) -> None: ...


class HttpxTransport:
    name = "httpx"

    def __init__(self, timeout: float = 30.0, http2: bool = True) -> None:
        import httpx

        self._httpx = httpx
        self._client = httpx.AsyncClient(
            http2=http2,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, read=max(timeout, 60.0)),
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        stream: bool = False,
    ) -> TransportResponse:
        try:
            req = self._client.build_request(method, url, headers=headers, params=params)
            resp = await self._client.send(req, stream=stream)
            if not stream:
                await resp.aread()
                return TransportResponse(
                    resp.status_code, dict(resp.headers), url=str(resp.url), content=resp.content
                )
            return TransportResponse(
                resp.status_code,
                dict(resp.headers),
                url=str(resp.url),
                stream=resp.aiter_bytes,
                close=resp.aclose,
            )
        except self._httpx.HTTPError as exc:
            raise TransportFailure(f"{exc.__class__.__name__}: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


class CurlCffiTransport:
    """Chrome TLS-fingerprint impersonation via curl_cffi (optional extra)."""

    name = "curl_cffi"

    def __init__(self, impersonate: str = "chrome", timeout: float = 30.0) -> None:
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise TransportUnavailable(
                "curl_cffi is not installed; install creeparr[impersonate]"
            ) from exc
        self._session = AsyncSession(impersonate=impersonate, timeout=timeout, allow_redirects=True)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        stream: bool = False,
    ) -> TransportResponse:
        try:
            resp = await self._session.request(
                method, url, headers=headers, params=params, stream=stream
            )
        except Exception as exc:  # noqa: BLE001
            raise TransportFailure(f"{exc.__class__.__name__}: {exc}") from exc
        hdrs = {k: v for k, v in resp.headers.items()}
        if not stream:
            return TransportResponse(
                resp.status_code, hdrs, url=str(resp.url), content=resp.content
            )

        async def _stream(chunk_size: int) -> AsyncIterator[bytes]:
            async for chunk in resp.aiter_content(chunk_size):
                yield chunk

        return TransportResponse(
            resp.status_code, hdrs, url=str(resp.url), stream=_stream, close=resp.aclose
        )

    async def aclose(self) -> None:
        await self._session.close()


def curl_cffi_available() -> bool:
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return False
    return True


def build_transport(backend: str, impersonate: str = "chrome") -> Transport:
    if backend == "curl_cffi":
        return CurlCffiTransport(impersonate=impersonate)
    return HttpxTransport()


class RateLimiter:
    """Global minimum-interval limiter with an optional random extra delay.

    The random delay (uniform between ``delay_min`` and ``delay_max`` seconds) is added
    to every request on top of the fixed interval, to make traffic look less robotic.
    """

    def __init__(
        self,
        requests_per_second: float = 1.0,
        random_delay: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self.delay_min = max(0.0, random_delay[0])
        self.delay_max = max(self.delay_min, random_delay[1])
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        if self.min_interval <= 0 and self.delay_max <= 0:
            return
        async with self._lock:
            if self.min_interval > 0:
                delay = self._last + self.min_interval - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            if self.delay_max > 0:
                await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))
            self._last = time.monotonic()

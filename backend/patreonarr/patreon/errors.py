"""Exceptions raised by the Patreon client."""

from __future__ import annotations


class PatreonError(Exception):
    """Base class for all Patreon client failures."""

    code = "patreon_error"

    def __init__(self, message: str = "", *, status: int | None = None, detail: str | None = None):
        super().__init__(message or self.__class__.__name__)
        self.status = status
        self.detail = detail


class TransportFailure(PatreonError):
    """Network-level failure (DNS, TLS, timeout, connection reset)."""

    code = "network"


class TransportUnavailable(PatreonError):
    """The requested HTTP backend is not installed."""

    code = "transport_unavailable"


class AuthError(PatreonError):
    """The session cookie is missing, invalid or expired."""

    code = "auth_invalid"


class ForbiddenError(PatreonError):
    """403 with a JSON body: usually a locked post, not necessarily a bad session."""

    code = "forbidden"


class CloudflareChallengeError(PatreonError):
    """Cloudflare served a challenge page instead of the API response."""

    code = "cloudflare_challenge"


class RateLimitedError(PatreonError):
    code = "rate_limited"

    def __init__(self, retry_after: float = 30.0, **kw):
        super().__init__(f"rate limited, retry after {retry_after:.0f}s", **kw)
        self.retry_after = retry_after


class NotFoundError(PatreonError):
    code = "not_found"


class TransientError(PatreonError):
    """5xx or otherwise retryable server failure."""

    code = "transient"


class UnexpectedResponse(PatreonError):
    code = "unexpected_response"

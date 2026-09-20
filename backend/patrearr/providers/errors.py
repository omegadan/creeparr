"""Exceptions shared by all provider clients."""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for provider client failures."""

    code = "provider_error"

    def __init__(self, message: str = "", *, status: int | None = None, detail: str | None = None):
        super().__init__(message or self.__class__.__name__)
        self.status = status
        self.detail = detail


class TransportFailure(ProviderError):
    """Network-level failure (DNS, TLS, timeout, connection reset)."""

    code = "network"


class TransportUnavailable(ProviderError):
    """The requested HTTP backend is not installed."""

    code = "transport_unavailable"


class AuthError(ProviderError):
    """The session is missing, invalid or expired."""

    code = "auth_invalid"


class ForbiddenError(ProviderError):
    """403 with a JSON body: usually a locked post, not necessarily a bad session."""

    code = "forbidden"


class CloudflareChallengeError(ProviderError):
    """Cloudflare served a challenge page instead of the API response."""

    code = "cloudflare_challenge"


class RateLimitedError(ProviderError):
    code = "rate_limited"

    def __init__(self, retry_after: float = 30.0, **kw):
        super().__init__(f"rate limited, retry after {retry_after:.0f}s", **kw)
        self.retry_after = retry_after


class NotFoundError(ProviderError):
    code = "not_found"


class TransientError(ProviderError):
    """5xx or otherwise retryable server failure."""

    code = "transient"


class UnexpectedResponse(ProviderError):
    code = "unexpected_response"


class NotConfigured(ProviderError):
    """The provider has no credentials yet."""

    code = "not_configured"

"""Backwards-compatible aliases; the shared definitions live in creeparr.providers.errors."""

from creeparr.providers.errors import (
    AuthError,
    CloudflareChallengeError,
    ForbiddenError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
    TransientError,
    TransportFailure,
    TransportUnavailable,
    UnexpectedResponse,
)

PatreonError = ProviderError

__all__ = [
    "AuthError",
    "CloudflareChallengeError",
    "ForbiddenError",
    "NotFoundError",
    "PatreonError",
    "ProviderError",
    "RateLimitedError",
    "TransientError",
    "TransportFailure",
    "TransportUnavailable",
    "UnexpectedResponse",
]

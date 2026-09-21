"""Application-level errors mapped to HTTP responses by the API layer."""

from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, detail: str | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        if code:
            self.code = code


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class UpstreamError(AppError):
    """Patreon returned something we could not work with."""

    status_code = 502
    code = "upstream_error"

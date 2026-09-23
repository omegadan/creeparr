"""UI authentication endpoints and the gate that protects the API."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import deque

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from creeparr.api.deps import get_services
from creeparr.core.errors import AppError, ValidationFailed
from creeparr.core.security import hash_password, make_token, verify_password, verify_token
from creeparr.services import Services

router = APIRouter(tags=["auth"])

COOKIE = "creeparr_session"
# Paths under /api/v1 that never require authentication.
OPEN_PATHS = ("/api/v1/auth/login", "/api/v1/auth/status")


# Wrong passwords allowed per client within the window before logins are refused.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 300
_failures: dict[str, deque[float]] = {}
_failures_lock = threading.Lock()


class TooManyAttempts(AppError):
    status_code = 429
    code = "too_many_attempts"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class LoginBody(BaseModel):
    password: str


class PasswordBody(BaseModel):
    password: str
    current_password: str | None = None


def auth_active(services: Services) -> bool:
    sec = services.settings.get().security
    return bool(sec.auth_enabled and sec.password_hash)


def session_key(services: Services) -> bytes:
    """Key that signs session cookies. It covers the password hash and the logout
    counter, so changing the password or logging out ends every existing session."""
    sec = services.settings.get().security
    material = f"{sec.password_hash}|{sec.session_epoch}".encode()
    return hmac.new(services.auth_secret, material, hashlib.sha256).digest()


def is_authenticated(request: Request, services: Services) -> bool:
    if not auth_active(services):
        return True
    token = request.cookies.get(COOKIE)
    return bool(token and verify_token(session_key(services), token))


def _recent_failures(client: str, now: float) -> deque[float]:
    times = _failures.setdefault(client, deque())
    while times and now - times[0] > LOGIN_WINDOW_SECONDS:
        times.popleft()
    return times


@router.get("/auth/status")
def auth_status(request: Request, services: Services = Depends(get_services)):
    return {
        "auth_enabled": auth_active(services),
        "authenticated": is_authenticated(request, services),
    }


@router.post("/auth/login")
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    services: Services = Depends(get_services),
):
    client = request.client.host if request.client else "?"
    now = time.monotonic()
    with _failures_lock:
        failures = _recent_failures(client, now)
        if len(failures) >= LOGIN_MAX_FAILURES:
            wait = int(LOGIN_WINDOW_SECONDS - (now - failures[0])) + 1
            raise TooManyAttempts(f"too many wrong passwords; try again in {wait} s")
    sec = services.settings.get().security
    if not sec.password_hash or not verify_password(body.password, sec.password_hash):
        with _failures_lock:
            _recent_failures(client, now).append(now)
        raise Unauthorized("incorrect password")
    with _failures_lock:
        _failures.pop(client, None)
    token = make_token(session_key(services))
    response.set_cookie(
        COOKIE, token, max_age=30 * 24 * 3600, httponly=True, samesite="lax", path="/"
    )
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response, services: Services = Depends(get_services)):
    # Tokens are stateless, so ending one means ending all: bump the signing key.
    # (Single-user app: "log out" logs out every browser.)
    if auth_active(services) and is_authenticated(request, services):
        epoch = services.settings.get().security.session_epoch
        services.settings.update({"security": {"session_epoch": epoch + 1}})
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.put("/auth/password")
def set_password(body: PasswordBody, request: Request, services: Services = Depends(get_services)):
    sec = services.settings.get().security
    # If a password already exists, require the current one (or an active session).
    if sec.password_hash and not is_authenticated(request, services):
        ok = body.current_password and verify_password(body.current_password, sec.password_hash)
        if not ok:
            raise Unauthorized("current password required")
    if len(body.password) < 6:
        raise ValidationFailed("password must be at least 6 characters")
    services.settings.update(
        {"security": {"password_hash": hash_password(body.password), "auth_enabled": True}},
        allow_secrets=True,
    )
    return {"ok": True}


@router.delete("/auth/password")
def disable_auth(request: Request, services: Services = Depends(get_services)):
    if not is_authenticated(request, services):
        raise Unauthorized("authentication required")
    services.settings.update(
        {"security": {"auth_enabled": False, "password_hash": ""}}, allow_secrets=True
    )
    return {"ok": True}

"""UI authentication endpoints and the gate that protects the API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from patrearr.api.deps import get_services
from patrearr.core.errors import AppError, ValidationFailed
from patrearr.core.security import hash_password, make_token, verify_password, verify_token
from patrearr.services import Services

router = APIRouter(tags=["auth"])

COOKIE = "patrearr_session"
# Paths under /api/v1 that never require authentication.
OPEN_PATHS = ("/api/v1/auth/login", "/api/v1/auth/status")


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class LoginBody(BaseModel):
    password: str


class PasswordBody(BaseModel):
    password: str
    current_password: str | None = None


def is_authenticated(request: Request, services: Services) -> bool:
    sec = services.settings.get().security
    if not sec.auth_enabled or not sec.password_hash:
        return True
    token = request.cookies.get(COOKIE)
    return bool(token and verify_token(services.auth_secret, token))


@router.get("/auth/status")
def auth_status(request: Request, services: Services = Depends(get_services)):
    sec = services.settings.get().security
    enabled = bool(sec.auth_enabled and sec.password_hash)
    return {"auth_enabled": enabled, "authenticated": is_authenticated(request, services)}


@router.post("/auth/login")
def login(body: LoginBody, response: Response, services: Services = Depends(get_services)):
    sec = services.settings.get().security
    if not sec.password_hash or not verify_password(body.password, sec.password_hash):
        raise Unauthorized("incorrect password")
    token = make_token(services.auth_secret)
    response.set_cookie(
        COOKIE, token, max_age=30 * 24 * 3600, httponly=True, samesite="lax", path="/"
    )
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response):
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

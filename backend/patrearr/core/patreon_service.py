"""Owns the PatreonClient instance and the account's auth status."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from patrearr.config import EnvConfig
from patrearr.core.events import EventBus
from patrearr.core.history import record_event
from patrearr.core.settings_service import SettingsService
from patrearr.core.state import get_state, set_state
from patrearr.db.engine import SessionFactory, session_scope
from patrearr.db.enums import AuthState, EventType
from patrearr.patreon.client import PatreonClient
from patrearr.patreon.cookies import CookieSet
from patrearr.patreon.errors import (
    AuthError,
    CloudflareChallengeError,
    PatreonError,
    TransportFailure,
    TransportUnavailable,
)
from patrearr.patreon.transport import RateLimiter, build_transport

log = logging.getLogger(__name__)

AUTH_KEY = "auth_status"


class PatreonService:
    def __init__(
        self,
        env: EnvConfig,
        settings: SettingsService,
        session_factory: SessionFactory,
        bus: EventBus,
    ) -> None:
        self.env = env
        self.settings = settings
        self._factory = session_factory
        self.bus = bus
        self._client: PatreonClient | None = None
        self._lock = asyncio.Lock()

    # ---- client lifecycle ----------------------------------------------------------

    def _build_client(
        self, session_id: str | None = None, cookies_txt: str | None = None
    ) -> PatreonClient:
        s = self.settings.get().patreon
        cookies = CookieSet.from_settings(
            session_id if session_id is not None else s.session_id,
            cookies_txt if cookies_txt is not None else s.cookies_txt,
        )
        try:
            transport = build_transport(s.http_backend, s.impersonate_target)
        except TransportUnavailable as exc:
            log.warning("%s; falling back to httpx", exc)
            transport = build_transport("httpx")
        return PatreonClient(
            transport, RateLimiter(s.requests_per_second), cookies, user_agent=s.user_agent
        )

    @property
    def client(self) -> PatreonClient:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    async def rebuild(self) -> None:
        async with self._lock:
            old = self._client
            self._client = self._build_client()
            if old is not None:
                await old.aclose()
        self.write_cookie_file()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def write_cookie_file(self) -> None:
        cookies = self.client.cookies
        try:
            if cookies.is_configured:
                cookies.write_netscape(self.env.cookies_file)
            elif self.env.cookies_file.exists():
                self.env.cookies_file.unlink()
        except OSError as exc:
            log.warning("could not write cookie file: %s", exc)

    # ---- auth status ---------------------------------------------------------------

    def get_auth_status(self) -> dict[str, Any]:
        with session_scope(self._factory) as s:
            status = get_state(s, AUTH_KEY)
        if not status:
            state = (
                AuthState.UNKNOWN
                if self.settings.get().patreon.session_id
                else AuthState.UNCONFIGURED
            )
            return {"state": state, "checked_at": None, "user_name": None, "error": None}
        return status

    @property
    def auth_ok(self) -> bool:
        return self.get_auth_status().get("state") in (AuthState.VALID, AuthState.UNKNOWN)

    def _set_auth_status(
        self,
        state: AuthState,
        *,
        user_name: str | None = None,
        user_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        status = {
            "state": state,
            "checked_at": datetime.now(UTC).isoformat(),
            "user_name": user_name,
            "user_id": user_id,
            "error": error,
        }
        with session_scope(self._factory) as s:
            previous = (get_state(s, AUTH_KEY) or {}).get("state")
            set_state(s, AUTH_KEY, status)
            if previous != state:
                if state == AuthState.VALID:
                    record_event(
                        s, self.bus, EventType.AUTH_VALID, f"Patreon session valid ({user_name})"
                    )
                elif state in (AuthState.INVALID, AuthState.CHALLENGE):
                    record_event(
                        s,
                        self.bus,
                        EventType.AUTH_INVALID,
                        f"Patreon session problem: {error}",
                        level="error",
                    )
        self.bus.publish("auth.status", status)
        return status

    def mark_auth_invalid(self, exc: PatreonError) -> None:
        state = (
            AuthState.CHALLENGE if isinstance(exc, CloudflareChallengeError) else AuthState.INVALID
        )
        log.error("Patreon auth problem: %s", exc)
        self._set_auth_status(state, error=str(exc))

    async def test_connection(
        self, session_id: str | None = None, cookies_txt: str | None = None
    ) -> dict[str, Any]:
        """Verify credentials. Uses submitted values if given, else the stored ones."""
        use_stored = session_id is None and cookies_txt is None
        client = self.client if use_stored else self._build_client(session_id, cookies_txt)
        try:
            user = await client.get_current_user()
        except AuthError as exc:
            result = {"ok": False, "reason": "auth_invalid", "detail": str(exc)}
            if use_stored:
                self._set_auth_status(AuthState.INVALID, error=str(exc))
            return result
        except CloudflareChallengeError as exc:
            result = {"ok": False, "reason": "cloudflare_challenge", "detail": str(exc)}
            if use_stored:
                self._set_auth_status(AuthState.CHALLENGE, error=str(exc))
            return result
        except TransportFailure as exc:
            if use_stored:
                self._set_auth_status(AuthState.ERROR, error=str(exc))
            return {"ok": False, "reason": "network", "detail": str(exc)}
        except PatreonError as exc:
            if use_stored:
                self._set_auth_status(AuthState.ERROR, error=str(exc))
            return {"ok": False, "reason": "unexpected", "detail": str(exc)}
        finally:
            if not use_stored:
                await client.aclose()
        if use_stored:
            self._set_auth_status(AuthState.VALID, user_name=user.full_name, user_id=user.id)
        return {
            "ok": True,
            "user": {"id": user.id, "full_name": user.full_name, "vanity": user.vanity},
        }

    async def check_session(self) -> bool:
        if not self.settings.get().patreon.session_id:
            self._set_auth_status(AuthState.UNCONFIGURED)
            return False
        result = await self.test_connection()
        return bool(result.get("ok"))

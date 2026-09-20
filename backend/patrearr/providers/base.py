"""Base class every content provider implements, plus shared auth-state handling."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from patrearr.config import EnvConfig
from patrearr.core.events import EventBus
from patrearr.core.history import record_event
from patrearr.core.settings_service import SettingsService
from patrearr.core.state import get_state, set_state
from patrearr.db.engine import SessionFactory, session_scope
from patrearr.db.enums import AuthState, EventType
from patrearr.patreon.transport import TransportResponse
from patrearr.providers.errors import (
    AuthError,
    CloudflareChallengeError,
    NotConfigured,
    ProviderError,
    TransportFailure,
)
from patrearr.providers.models import (
    CreatorInfo,
    MediaSpec,
    PostPage,
    PostResource,
    SubscriptionInfo,
    UserInfo,
)

log = logging.getLogger(__name__)


class ProviderService(ABC):
    """One per provider. Owns the HTTP client and the account's auth status."""

    name: ClassVar[str] = "base"
    label: ClassVar[str] = "Base"
    #: Secret settings keys (group == provider name) accepted by the auth endpoints.
    credential_fields: ClassVar[tuple[str, ...]] = ()
    #: Whether creators need the provider's session to download their native media.
    supports_embeds: ClassVar[bool] = False

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
        self._lock = asyncio.Lock()

    # ---- settings helpers ----------------------------------------------------------

    def group_settings(self) -> Any:
        return getattr(self.settings.get(), self.name)

    @property
    @abstractmethod
    def is_configured(self) -> bool: ...

    @property
    def cookie_file(self) -> Path:
        return self.env.cookies_dir / f"{self.name}.txt"

    @property
    def ytdlp_impersonate(self) -> bool:
        return False

    # ---- lifecycle -----------------------------------------------------------------

    @abstractmethod
    async def rebuild(self) -> None:
        """Rebuild the HTTP client after settings changed."""

    @abstractmethod
    async def aclose(self) -> None: ...

    def write_cookie_file(self) -> None:  # noqa: B027
        """Write a cookies.txt for yt-dlp/ffmpeg if the provider needs one."""

    # ---- provider API ----------------------------------------------------------------

    @abstractmethod
    async def fetch_user(self, credentials: dict[str, str] | None = None) -> UserInfo:
        """Return the logged-in user, using submitted credentials if given."""

    @abstractmethod
    async def resolve_creator(self, query: str) -> CreatorInfo: ...

    @abstractmethod
    async def get_creator(self, external_id: str) -> CreatorInfo: ...

    @abstractmethod
    async def list_subscriptions(self) -> list[SubscriptionInfo]: ...

    @abstractmethod
    def iter_posts(self, external_id: str) -> AsyncIterator[PostPage]: ...

    def iter_sources(self, external_id: str) -> list[tuple[str, AsyncIterator[PostPage]]]:
        """Ordered (source name, page iterator) pairs. Each source is scanned separately
        so incremental scans get an independent overlap window per source."""
        return [("posts", self.iter_posts(external_id))]

    @abstractmethod
    async def get_post(self, external_id: str, post_id: str) -> PostResource: ...

    @abstractmethod
    def resolve_media(self, post: PostResource) -> list[MediaSpec]: ...

    @abstractmethod
    def post_from_raw(self, raw_json: dict[str, Any]) -> PostResource | None:
        """Rebuild a PostResource from what we stored in `posts.raw_json`."""

    @abstractmethod
    def media_headers(self) -> dict[str, str]: ...

    @abstractmethod
    async def fetch_text(self, url: str) -> str: ...

    @abstractmethod
    async def stream(self, url: str, *, range_start: int = 0) -> TransportResponse: ...

    # ---- auth status (shared) ------------------------------------------------------

    @property
    def _state_key(self) -> str:
        return f"auth_status:{self.name}"

    def get_auth_status(self) -> dict[str, Any]:
        with session_scope(self._factory) as s:
            status = get_state(s, self._state_key)
            if not status and self.name == "patreon":
                status = get_state(s, "auth_status")  # pre-provider key
        if not status:
            state = AuthState.UNKNOWN if self.is_configured else AuthState.UNCONFIGURED
            return {
                "provider": self.name,
                "state": state,
                "checked_at": None,
                "user_name": None,
                "error": None,
            }
        status.setdefault("provider", self.name)
        return status

    @property
    def auth_ok(self) -> bool:
        return self.get_auth_status().get("state") in (AuthState.VALID, AuthState.UNKNOWN)

    @property
    def auth_blocked(self) -> bool:
        return self.get_auth_status().get("state") in (
            AuthState.INVALID,
            AuthState.CHALLENGE,
            AuthState.UNCONFIGURED,
        )

    def _set_auth_status(
        self,
        state: AuthState,
        *,
        user_name: str | None = None,
        user_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        status = {
            "provider": self.name,
            "state": state,
            "checked_at": datetime.now(UTC).isoformat(),
            "user_name": user_name,
            "user_id": user_id,
            "error": error,
        }
        with session_scope(self._factory) as s:
            previous = (get_state(s, self._state_key) or {}).get("state")
            set_state(s, self._state_key, status)
            if previous != state:
                if state == AuthState.VALID:
                    record_event(
                        s,
                        self.bus,
                        EventType.AUTH_VALID,
                        f"{self.label} session valid ({user_name})",
                        data={"provider": self.name},
                    )
                elif state in (AuthState.INVALID, AuthState.CHALLENGE):
                    record_event(
                        s,
                        self.bus,
                        EventType.AUTH_INVALID,
                        f"{self.label} session problem: {error}",
                        level="error",
                        data={"provider": self.name},
                    )
        self.bus.publish("auth.status", status)
        return status

    def mark_auth_invalid(self, exc: ProviderError) -> None:
        state = (
            AuthState.CHALLENGE if isinstance(exc, CloudflareChallengeError) else AuthState.INVALID
        )
        log.error("%s auth problem: %s", self.label, exc)
        self._set_auth_status(state, error=str(exc))

    async def test_connection(self, credentials: dict[str, str] | None = None) -> dict[str, Any]:
        """Verify credentials. Submitted values are tested without being stored."""
        use_stored = not credentials
        try:
            user = await self.fetch_user(None if use_stored else credentials)
        except NotConfigured as exc:
            if use_stored:
                self._set_auth_status(AuthState.UNCONFIGURED)
            return {"ok": False, "reason": "not_configured", "detail": str(exc)}
        except AuthError as exc:
            if use_stored:
                self._set_auth_status(AuthState.INVALID, error=str(exc))
            return {"ok": False, "reason": "auth_invalid", "detail": str(exc)}
        except CloudflareChallengeError as exc:
            if use_stored:
                self._set_auth_status(AuthState.CHALLENGE, error=str(exc))
            return {"ok": False, "reason": "cloudflare_challenge", "detail": str(exc)}
        except TransportFailure as exc:
            if use_stored:
                self._set_auth_status(AuthState.ERROR, error=str(exc))
            return {"ok": False, "reason": "network", "detail": str(exc)}
        except ProviderError as exc:
            if use_stored:
                self._set_auth_status(AuthState.ERROR, error=str(exc))
            return {"ok": False, "reason": "unexpected", "detail": str(exc)}
        if use_stored:
            self._set_auth_status(AuthState.VALID, user_name=user.full_name, user_id=user.id)
        return {
            "ok": True,
            "user": {"id": user.id, "full_name": user.full_name, "vanity": user.vanity},
        }

    async def check_session(self) -> bool:
        if not self.is_configured:
            self._set_auth_status(AuthState.UNCONFIGURED)
            return False
        result = await self.test_connection()
        return bool(result.get("ok"))

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "configured": self.is_configured,
            "credential_fields": list(self.credential_fields),
            "auth": self.get_auth_status(),
        }

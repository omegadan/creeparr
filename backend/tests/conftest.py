from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from patrearr.config import EnvConfig
from patrearr.core.events import EventBus
from patrearr.core.settings_service import SettingsService
from patrearr.db.engine import make_engine, make_session_factory
from patrearr.db.migrate import run_migrations
from patrearr.patreon.client import PatreonClient
from patrearr.patreon.cookies import CookieSet
from patrearr.patreon.transport import HttpxTransport, RateLimiter


@pytest.fixture
def env(tmp_path: Path) -> EnvConfig:
    e = EnvConfig(
        config_dir=tmp_path / "config", download_dir=tmp_path / "downloads", log_level="DEBUG"
    )
    e.ensure_dirs()
    return e


@pytest.fixture
def engine(env: EnvConfig):
    run_migrations(env.database_url)
    eng = make_engine(env.database_url)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def settings(session_factory) -> SettingsService:
    return SettingsService(session_factory)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def respx_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


def make_client(session_id: str | None = "sid-123", rps: float = 1000.0) -> PatreonClient:
    return PatreonClient(
        HttpxTransport(http2=False), RateLimiter(rps), CookieSet(session_id=session_id)
    )


@pytest.fixture
async def client() -> PatreonClient:
    c = make_client()
    yield c
    await c.aclose()


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch):
    """Make retry back-offs instantaneous in tests."""
    real_sleep = asyncio.sleep

    async def fast(seconds: float, *a, **k):
        await real_sleep(0 if seconds > 0.05 else seconds)

    monkeypatch.setattr("patrearr.patreon.client.asyncio.sleep", fast)


def json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, json=payload, headers={"content-type": "application/vnd.api+json"}
    )

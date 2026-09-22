from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from creeparr.app import create_app
from creeparr.db.enums import AuthState
from creeparr.patreon.client import API_URL
from tests import patreon_fixtures as fx
from tests.conftest import json_response

# What the web UI sends on every request (see creeparr.api.guard).
UI_HEADERS = {"X-Requested-With": "creeparr"}


@pytest.fixture
def app(env):
    application = create_app(env, start_background=False)
    services = application.state.services
    services.settings.update(
        {"patreon": {"session_id": "sid-123", "requests_per_second": 10}}, allow_secrets=True
    )
    services.providers.get("patreon")._set_auth_status(AuthState.VALID, user_name="Test Patron")
    return application


@pytest.fixture
def api(app):
    with TestClient(app, headers=UI_HEADERS) as c:
        yield c


def test_settings_masking_and_secret_protection(api):
    r = api.get("/api/v1/settings")
    assert r.status_code == 200
    assert r.json()["patreon"]["session_id"] == "••••"  # short values are fully masked
    r = api.put(
        "/api/v1/settings", json={"patreon": {"session_id": "evil", "requests_per_second": 2}}
    )
    assert r.status_code == 200
    assert r.json()["patreon"]["requests_per_second"] == 2
    assert api.app.state.services.settings.get().patreon.session_id == "sid-123"
    r = api.put("/api/v1/settings", json={"nope": {}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"


def test_patreon_auth_roundtrip(api, respx_mock):
    respx_mock.get(f"{API_URL}/current_user").mock(
        return_value=json_response(fx.current_user_response())
    )
    r = api.post("/api/v1/settings/auth/patreon/test", json={"session_id": "candidate"})
    assert (
        r.status_code == 200 and r.json()["ok"] and r.json()["user"]["full_name"] == "Test Patron"
    )
    r = api.put("/api/v1/settings/auth/patreon", json={"session_id": "newsid-abcdef"})
    assert r.status_code == 200 and r.json()["ok"]
    assert r.json()["settings"]["session_id"].endswith("cdef")
    status = api.get("/api/v1/system/status").json()
    assert status["auth"]["state"] == "valid" and status["auth"]["user_name"] == "Test Patron"
    respx_mock.get(f"{API_URL}/current_user").mock(
        return_value=httpx.Response(401, json={"errors": [{"detail": "nope"}]})
    )
    r = api.post("/api/v1/settings/auth/patreon/test")
    assert r.json() == {"ok": False, "reason": "auth_invalid", "detail": r.json()["detail"]}
    assert api.get("/api/v1/system/status").json()["auth"]["state"] == "invalid"


def test_lookup_add_and_list_creator(api, respx_mock):
    respx_mock.get(f"{API_URL}/search").mock(return_value=json_response(fx.search_response()))
    respx_mock.get(f"{API_URL}/campaigns/{fx.CAMPAIGN_ID}").mock(
        return_value=json_response(fx.campaign_response())
    )
    r = api.post(
        "/api/v1/creators/lookup", json={"query": "https://www.patreon.com/c/examplecreator"}
    )
    assert r.status_code == 200
    assert r.json()["external_id"] == fx.CAMPAIGN_ID and not r.json()["already_added"]

    r = api.post("/api/v1/creators", json={"query": "examplecreator", "include_images": True})
    assert r.status_code == 201, r.text
    creator = r.json()
    assert (
        creator["name"] == "Example Creator" and creator["include_images"] and creator["monitored"]
    )
    assert creator["scanning"] is True  # full scan requested on add

    r = api.post("/api/v1/creators", json={"query": "examplecreator"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_added"

    r = api.post("/api/v1/creators/lookup", json={"query": "examplecreator"})
    assert r.json()["already_added"] and r.json()["creator_id"] == creator["id"]

    r = api.get("/api/v1/creators")
    assert [c["id"] for c in r.json()] == [creator["id"]]

    r = api.patch(
        f"/api/v1/creators/{creator['id']}", json={"monitored": False, "folder_name": " Custom "}
    )
    assert r.json()["monitored"] is False and r.json()["folder_name"] == "Custom"

    r = api.delete(f"/api/v1/creators/{creator['id']}")
    assert r.status_code == 204
    assert api.get("/api/v1/creators").json() == []
    assert api.get(f"/api/v1/creators/{creator['id']}").status_code == 404


def test_add_unknown_creator_404(api, respx_mock):
    respx_mock.get(f"{API_URL}/search").mock(return_value=json_response({"data": []}))
    respx_mock.get("https://www.patreon.com/nobody").mock(
        return_value=httpx.Response(
            200, text="<html></html>", headers={"content-type": "text/html"}
        )
    )
    r = api.post("/api/v1/creators", json={"query": "nobody"})
    assert r.status_code == 404


def test_import_pledges(api, respx_mock):
    respx_mock.get(f"{API_URL}/current_user").mock(
        return_value=json_response(fx.pledges_response())
    )
    r = api.get("/api/v1/providers/patreon/subscriptions")
    assert r.status_code == 200 and len(r.json()) == 2
    r = api.post(
        "/api/v1/creators/import-subscriptions",
        json={"provider": "patreon", "ids": [fx.CAMPAIGN_ID, "7654321"]},
    )
    assert r.status_code == 201 and sorted(c["campaign_id"] for c in r.json()) == [
        fx.CAMPAIGN_ID,
        "7654321",
    ]
    r = api.get("/api/v1/providers/patreon/subscriptions")
    assert all(p["already_added"] for p in r.json())


def test_queue_pause_resume_and_scan_conflict_when_auth_invalid(api):
    assert api.post("/api/v1/queue/pause").json()["paused"] is True
    assert api.get("/api/v1/queue").json()["paused"] is True
    assert api.post("/api/v1/queue/resume").json()["paused"] is False
    api.app.state.services.providers.get("patreon")._set_auth_status(AuthState.INVALID, error="x")
    r = api.post("/api/v1/creators/scan-all")
    assert r.status_code == 202 and r.json()["queued"] == 0


def test_queue_failed_routes_not_shadowed_by_job_id(api):
    # DELETE /queue/failed and POST /queue/retry-failed must not be captured by
    # the /queue/{job_id} routes (which only accept integers).
    r = api.delete("/api/v1/queue/failed")
    assert r.status_code == 200, r.text
    assert "cleared" in r.json()
    r = api.post("/api/v1/queue/retry-failed")
    assert r.status_code == 200, r.text
    assert "requeued" in r.json()
    # A real integer job id still 404s cleanly rather than 422.
    assert api.delete("/api/v1/queue/999999").status_code == 404


def test_history_and_logs(api):
    r = api.get("/api/v1/history")
    assert r.status_code == 200 and r.json()["total"] >= 1  # auth_valid event from fixture
    r = api.get("/api/v1/system/logs?lines=10")
    assert r.status_code == 200 and "lines" in r.json()


def test_ui_auth_flow(env):
    from creeparr.app import create_app

    app = create_app(env, start_background=False)
    with TestClient(app, headers=UI_HEADERS) as c:
        assert c.get("/api/v1/auth/status").json()["auth_enabled"] is False
        assert c.get("/api/v1/creators").status_code == 200
        assert c.put("/api/v1/auth/password", json={"password": "hunter2"}).status_code == 200
        assert c.get("/api/v1/creators").status_code == 401
        assert c.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401
        assert c.post("/api/v1/auth/login", json={"password": "hunter2"}).status_code == 200
        assert c.get("/api/v1/creators").status_code == 200
        assert c.delete("/api/v1/auth/password").status_code == 200
        assert c.get("/api/v1/creators").status_code == 200  # auth disabled again


def test_state_changing_calls_need_the_ui_header(app):
    # A cross-site page can send a bodyless POST but not a custom header (that needs a
    # CORS preflight this app never approves), so the header is the CSRF guard.
    with TestClient(app) as c:
        r = c.post("/api/v1/queue/pause")
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_header_missing"
        assert c.get("/api/v1/queue").status_code == 200  # reads don't need it
        assert c.post("/api/v1/queue/pause", headers=UI_HEADERS).status_code == 200


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("192.168.1.20:7979", True),
        ("[fd00::1]:7979", True),
        ("localhost:7979", True),
        ("tower:7979", True),  # bare LAN name (Unraid)
        ("tower.local", True),
        ("nas.home.arpa:7979", True),
        ("tower.tail1234.ts.net", True),
        ("evil.example:7979", False),  # DNS rebinding
        ("creeparr.mydomain.com", False),
    ],
)
def test_host_check_without_password(app, host, allowed):
    with TestClient(app, headers=UI_HEADERS) as c:
        r = c.get("/api/v1/creators", headers={"host": host})
        assert (r.status_code == 200) is allowed, r.text
        if not allowed:
            assert r.json()["error"]["code"] == "host_not_allowed"
        assert c.get("/health", headers={"host": host}).status_code == 200


def test_allowed_hosts_env_and_password_lift_host_check(env):
    env.allowed_hosts = ".mydomain.com, other.example"
    app = create_app(env, start_background=False)
    with TestClient(app, headers=UI_HEADERS) as c:
        assert c.get("/api/v1/creators", headers={"host": "creeparr.mydomain.com"}).is_success
        assert c.get("/api/v1/creators", headers={"host": "other.example:7979"}).is_success
        assert c.get("/api/v1/creators", headers={"host": "evil.example"}).status_code == 403
        # With a password set, an unknown host is no longer the attacker's way in: it
        # has no session, so it just gets 401 like any unauthenticated client.
        assert c.put("/api/v1/auth/password", json={"password": "hunter2"}).status_code == 200
        c.cookies.clear()
        assert c.get("/api/v1/creators", headers={"host": "evil.example"}).status_code == 401

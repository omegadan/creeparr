"""Provider-client edge cases: error bodies, pagination guards, host and URL checks."""

from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from creeparr.core.security import get_secret_key
from creeparr.patreon.client import API_URL
from creeparr.providers.cookies import CookieSet
from creeparr.providers.errors import AuthError, NotFoundError, UnexpectedResponse
from creeparr.providers.http import TransportResponse
from creeparr.providers.onlyfans.client import OnlyFansClient
from creeparr.providers.reddit.provider import RedditProvider
from creeparr.providers.youtube import YouTubeProvider
from tests import patreon_fixtures as fx
from tests.conftest import json_response


def test_retry_after_accepts_seconds_and_http_dates():
    from creeparr.providers.http import parse_retry_after

    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(None) == 30.0
    assert parse_retry_after("soon") == 30.0
    later = format_datetime(datetime.now(UTC) + timedelta(seconds=90), usegmt=True)
    assert 80 <= parse_retry_after(later) <= 91  # used to crash float()


def _of_parse(status: int, body: str):
    client = object.__new__(OnlyFansClient)
    return client._parse(TransportResponse(status, {}, content=body.encode()))


def test_onlyfans_errors_do_not_read_as_empty_lists():
    # A 400 used to be parsed as data, so a scan saw "no posts" and ended as a success.
    with pytest.raises(AuthError):
        _of_parse(400, '{"error": {"code": 0, "message": "Please refresh the page"}}')
    with pytest.raises(UnexpectedResponse):
        _of_parse(400, '{"error": {"message": "Bad request"}}')
    with pytest.raises(UnexpectedResponse):
        _of_parse(200, '{"error": {"message": "Something broke"}}')
    assert _of_parse(200, '{"list": [], "hasMore": false}') == {"list": [], "hasMore": False}
    assert _of_parse(200, '[{"id": 1}]') == [{"id": 1}]


@pytest.mark.asyncio
async def test_patreon_stops_when_a_page_links_back(client, respx_mock):
    # A next link pointing at an already-fetched page used to loop forever.
    loop_url = f"{API_URL}/posts?page[cursor]=same"
    first = fx.posts_page([fx.native_video_post("p1")], next_url=loop_url)
    again = fx.posts_page([fx.native_video_post("p2")], next_url=loop_url)
    respx_mock.get(url__startswith=f"{API_URL}/posts").mock(
        side_effect=[json_response(first), json_response(again), json_response(again)]
    )
    pages = [page async for page in client.iter_posts(fx.CAMPAIGN_ID)]
    assert len(pages) == 2


def test_reddit_bare_subreddit_url_is_not_a_crash():
    with pytest.raises(NotFoundError):
        RedditProvider._target("https://reddit.com/r/")  # was IndexError -> HTTP 500
    assert RedditProvider._target("https://www.reddit.com/r/aww/") == "r/aww"


def test_youtube_lookup_rejects_other_sites():
    # Any URL containing "youtube.com" used to go to yt-dlp's generic extractor.
    with pytest.raises(NotFoundError):
        YouTubeProvider._channel_videos_url("https://evil.example/?next=youtube.com")
    assert (
        YouTubeProvider._channel_videos_url("https://www.youtube.com/@someone/featured")
        == "https://www.youtube.com/@someone/videos"
    )
    assert YouTubeProvider._channel_videos_url("@someone").endswith("/@someone/videos")


def test_cookie_domain_must_match_exactly_or_as_subdomain():
    txt = (
        ".patreon.com\tTRUE\t/\tTRUE\t0\tcf_clearance\tgood\n"
        ".notpatreon.com\tTRUE\t/\tTRUE\t0\ttracker\tbad\n"
        "www.patreon.com\tFALSE\t/\tTRUE\t0\tsession_id\tsid\n"
    )
    cs = CookieSet.from_settings(None, txt)
    assert cs.session_id == "sid"
    assert cs.extra == {"cf_clearance": "good"}


def test_secret_key_file_is_private_from_creation(tmp_path):
    get_secret_key(tmp_path)
    mode = stat.S_IMODE((tmp_path / "secret.key").stat().st_mode)
    assert mode == 0o600


@pytest.mark.asyncio
async def test_onlyfans_stream_closes_its_one_off_client(
    env, settings, session_factory, bus, respx_mock, monkeypatch
):
    from creeparr.providers.http import HttpxTransport
    from creeparr.providers.onlyfans.provider import OnlyFansProvider

    closed: list[bool] = []

    class Tracking(HttpxTransport):
        async def aclose(self):
            closed.append(True)
            await super().aclose()

    prov = OnlyFansProvider(env, settings, session_factory, bus)
    monkeypatch.setattr(prov, "_transport", lambda: Tracking(http2=False))
    respx_mock.get("https://cdn2.onlyfans.com/v.mp4").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    resp = await prov.stream("https://cdn2.onlyfans.com/v.mp4")
    assert not closed
    await resp.aclose()
    assert closed == [True]  # used to leak one HTTP client per downloaded file

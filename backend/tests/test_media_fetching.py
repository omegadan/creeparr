"""Each provider fetching its media, through the provider interface: whether link renewal
applies, its media session and the one stream, who hosts embeds, and DRM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from creeparr.db.enums import MediaSource
from creeparr.providers.errors import ProviderError, TransportFailure
from creeparr.providers.instagram.client import group_into_posts
from creeparr.providers.instagram.provider import InstagramProvider
from creeparr.providers.media import MediaSession
from creeparr.providers.onlyfans.client import MESSAGE_POST_TYPE
from creeparr.providers.onlyfans.provider import OnlyFansProvider
from creeparr.providers.patreon import PatreonProvider
from creeparr.providers.reddit.provider import RedditProvider
from creeparr.providers.registry import ProviderRegistry
from creeparr.providers.youtube import YouTubeProvider
from tests import patreon_fixtures as fx
from tests.test_instagram import _items


@dataclass(frozen=True)
class Ref:
    """A stored media item, as a provider is told about it."""

    source: str = MediaSource.NATIVE_DIRECT
    post_type: str | None = None
    url: str | None = "https://cdn.example/f.mp4"
    remote_metadata: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def make(env, settings, session_factory, bus):
    def _make(cls):  # noqa: ANN001, ANN202
        return cls(env, settings, session_factory, bus)

    return _make


@pytest.fixture
async def patreon(env, settings, session_factory, bus):
    settings.update(
        {"patreon": {"session_id": "sid-123", "requests_per_second": 10}}, allow_secrets=True
    )
    prov = PatreonProvider(env, settings, session_factory, bus)
    yield prov
    await prov.aclose()


# ---- link renewal --------------------------------------------------------------------


def _ig_type(source: str) -> str:
    return group_into_posts(_items()[:1], source, "someone")[0].post_type


@pytest.mark.parametrize(
    ("cls", "ref", "renews"),
    [
        (PatreonProvider, Ref(), True),
        (PatreonProvider, Ref(MediaSource.NATIVE_HLS), True),
        (PatreonProvider, Ref(MediaSource.EMBED_YOUTUBE), False),
        (PatreonProvider, Ref(MediaSource.EMBED_VIMEO), False),
        (OnlyFansProvider, Ref(post_type="onlyfans_post"), True),
        (OnlyFansProvider, Ref(post_type=MESSAGE_POST_TYPE), False),
        (InstagramProvider, Ref(post_type="instagram_post"), True),
        (InstagramProvider, Ref(post_type="instagram_reel"), True),
        (InstagramProvider, Ref(post_type="instagram_tagged"), True),
        (InstagramProvider, Ref(post_type="instagram_story"), False),
        (InstagramProvider, Ref(post_type="instagram_highlight"), False),
        (RedditProvider, Ref(), False),
        (YouTubeProvider, Ref(MediaSource.EMBED_YOUTUBE), False),
    ],
)
def test_whether_link_renewal_applies(make, cls, ref, renews):
    assert make(cls).renews_links(ref) is renews


def test_instagram_rules_use_the_post_types_its_scans_store(make):
    prov = make(InstagramProvider)
    assert _ig_type("stories") == "instagram_story"  # was "instagram_storie"
    assert not prov.renews_links(Ref(post_type=_ig_type("stories")))
    assert not prov.renews_links(Ref(post_type=_ig_type("highlights")))
    for source in ("posts", "reels", "tagged"):
        assert prov.renews_links(Ref(post_type=_ig_type(source)))


@pytest.mark.asyncio
async def test_instagram_renews_a_reel_as_a_reel(make, monkeypatch):
    prov = make(InstagramProvider)
    urls: list[str] = []

    class FakeClient:
        def extract(self, url: str):  # noqa: ANN202
            urls.append(url)
            return _items()[:1]

    monkeypatch.setattr(prov, "_client", lambda *a, **k: FakeClient())
    reel = await prov.get_post("someone", "AAA", post_type="instagram_reel")
    assert reel.post_type == "instagram_reel"
    assert urls == ["https://www.instagram.com/p/AAA/"]
    plain = await prov.get_post("someone", "AAA")
    assert plain.post_type == "instagram_post"


# ---- the media session and its stream ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "sends_cookie"),
    [
        ("https://www.patreon.com/file/x", True),
        ("https://patreon.com/file/x", True),
        ("https://c10.patreonusercontent.com/file.mp4", False),
        ("https://evilpatreon.com/x", False),
        ("https://www.patreon.com.evil.example/x", False),
    ],
)
async def test_patreon_cookie_only_goes_to_patreon_hosts(patreon, respx_mock, url, sends_cookie):
    route = respx_mock.get(url).mock(return_value=httpx.Response(200, content=b"x"))
    resp = await patreon.media_session().stream(url, range_start=1)
    await resp.aclose()
    req = route.calls[0].request
    assert ("cookie" in req.headers) is sends_cookie
    assert req.headers["range"] == "bytes=1-"
    assert req.headers["referer"] == "https://www.patreon.com/"


@pytest.mark.asyncio
async def test_patreon_paces_files_with_its_api_requests(patreon, respx_mock, monkeypatch):
    waits: list[bool] = []
    limiter = patreon.client.limiter

    async def wait() -> None:
        waits.append(True)

    monkeypatch.setattr(limiter, "wait", wait)
    respx_mock.get(f"{fx.CDN}/f.mp4").mock(return_value=httpx.Response(200, content=b"x"))
    resp = await patreon.media_session().stream(f"{fx.CDN}/f.mp4")
    await resp.aclose()
    assert waits == [True]


@pytest.mark.asyncio
async def test_a_youtube_cover_streams(make, respx_mock):
    # YouTube's stream used to refuse every request, so its videos had no cover embedded.
    url = "https://i.ytimg.com/vi/abc/maxresdefault.jpg"
    respx_mock.get(url).mock(return_value=httpx.Response(200, content=b"jpeg"))
    resp = await make(YouTubeProvider).media_session().stream(url)
    assert resp.status == 200
    assert b"".join([c async for c in resp.aiter_bytes()]) == b"jpeg"
    await resp.aclose()


@pytest.mark.asyncio
async def test_a_dropped_connection_is_retried_once(respx_mock):
    url = "https://cdn.example/f.mp4"
    route = respx_mock.get(url).mock(
        side_effect=[httpx.ConnectError("reset"), httpx.Response(200, content=b"x")]
    )
    resp = await MediaSession().stream(url)
    assert resp.status == 200
    await resp.aclose()
    assert route.call_count == 2

    route.side_effect = [httpx.ConnectError("reset"), httpx.ConnectError("reset")]
    with pytest.raises(TransportFailure):
        await MediaSession().stream(url)


def test_the_cookie_file_is_handed_on_only_once_written(make):
    prov = make(YouTubeProvider)
    assert prov.media_session().cookie_file is None
    prov.cookie_file.parent.mkdir(parents=True, exist_ok=True)
    prov.cookie_file.write_text("# Netscape HTTP Cookie File\n")
    assert prov.media_session().cookie_file == prov.cookie_file


# ---- who hosts embeds ----------------------------------------------------------------


def test_youtube_hosts_youtube_videos_whoever_posted_them(make):
    youtube = make(YouTubeProvider)
    registry = ProviderRegistry([make(PatreonProvider), youtube, make(RedditProvider)])
    assert registry.hosting(MediaSource.EMBED_YOUTUBE) is youtube
    assert registry.hosting(MediaSource.EMBED_VIMEO) is None
    assert registry.hosting(MediaSource.NATIVE_DIRECT) is None


def test_two_providers_hosting_one_source_is_refused(make):
    class OtherTube(RedditProvider):
        name = "othertube"
        hosts = frozenset({MediaSource.EMBED_YOUTUBE})

    with pytest.raises(ValueError, match="hosted by both"):
        ProviderRegistry([make(YouTubeProvider), make(OtherTube)])


# ---- DRM -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drm_flagged_when_read_needs_no_check(make):
    prov = make(OnlyFansProvider)
    assert await prov.drm_protected(Ref(remote_metadata={"drm": True}))
    assert not await prov.drm_protected(Ref())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("master", "variant", "drm"),
    [
        (fx.MUX_MASTER_DRM, fx.MUX_VARIANT_CLEAR, True),
        (fx.MUX_MASTER_CLEAR, fx.MUX_VARIANT_WIDEVINE, True),
        (fx.MUX_MASTER_CLEAR, fx.MUX_VARIANT_CLEAR, False),
    ],
)
async def test_patreon_checks_its_streams_for_drm(patreon, respx_mock, master, variant, drm):
    base = fx.MUX_URL.split("?")[0]
    respx_mock.get(base).mock(return_value=httpx.Response(200, text=master))
    respx_mock.get(url__regex=r"https://stream\.mux\.com/rendition_.*").mock(
        return_value=httpx.Response(200, text=variant)
    )
    assert await patreon.drm_protected(Ref(MediaSource.NATIVE_HLS, url=fx.MUX_URL)) is drm


@pytest.mark.asyncio
async def test_patreon_checks_only_its_streams(patreon, respx_mock):
    # Files and embeds make no request (respx refuses unmocked ones).
    assert not await patreon.drm_protected(Ref(MediaSource.NATIVE_DIRECT))
    assert not await patreon.drm_protected(Ref(MediaSource.EMBED_VIMEO))


@pytest.mark.asyncio
async def test_patreon_says_when_it_cant_tell(patreon, respx_mock):
    respx_mock.get(fx.MUX_URL.split("?")[0]).mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderError):
        await patreon.drm_protected(Ref(MediaSource.NATIVE_HLS, url=fx.MUX_URL))

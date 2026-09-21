from __future__ import annotations

import pytest

from creeparr.patreon.drm import first_variant_uri, is_drm_playlist, probe_hls_drm
from tests import patreon_fixtures as fx


def test_is_drm_playlist():
    assert not is_drm_playlist(fx.MUX_MASTER_CLEAR)
    assert not is_drm_playlist(fx.MUX_VARIANT_CLEAR)
    assert not is_drm_playlist(fx.MUX_VARIANT_AES128)
    assert is_drm_playlist(fx.MUX_MASTER_DRM)
    assert is_drm_playlist(fx.MUX_VARIANT_WIDEVINE)


def test_first_variant_uri():
    uri = first_variant_uri(fx.MUX_MASTER_CLEAR, "https://stream.mux.com/abc.m3u8?token=y")
    assert uri == "https://stream.mux.com/rendition_720.m3u8?token=x"


@pytest.mark.asyncio
async def test_probe_checks_variant():
    texts = {
        "https://x/master.m3u8": fx.MUX_MASTER_CLEAR,
        "https://x/rendition_720.m3u8?token=x": fx.MUX_VARIANT_WIDEVINE,
    }

    async def fetch(url: str) -> str:
        return texts[url]

    assert await probe_hls_drm(fetch, "https://x/master.m3u8") is True
    texts["https://x/rendition_720.m3u8?token=x"] = fx.MUX_VARIANT_CLEAR
    assert await probe_hls_drm(fetch, "https://x/master.m3u8") is False

"""Detect DRM-protected HLS playlists so we never waste retries on them."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

DRM_KEYFORMATS = (
    "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",  # Widevine
    "urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95",  # PlayReady
    "com.apple.streamingkeydelivery",  # FairPlay
    "com.microsoft.playready",
    "com.widevine",
)
DRM_METHODS = ("SAMPLE-AES", "SAMPLE-AES-CTR", "SAMPLE-AES-CENC")

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("([^"]*)"|([^,]*))')


def _parse_attrs(line: str) -> dict[str, str]:
    _, _, rest = line.partition(":")
    return {
        m.group(1): (m.group(3) if m.group(3) is not None else m.group(4))
        for m in _ATTR_RE.finditer(rest)
    }


def is_drm_playlist(text: str) -> bool:
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("#EXT-X-KEY") or line.startswith("#EXT-X-SESSION-KEY")):
            continue
        attrs = _parse_attrs(line)
        method = attrs.get("METHOD", "").upper()
        keyformat = attrs.get("KEYFORMAT", "").lower()
        if method in DRM_METHODS:
            return True
        if any(k in keyformat for k in (kf.lower() for kf in DRM_KEYFORMATS)):
            return True
    return False


def first_variant_uri(master_text: str, base_url: str) -> str | None:
    lines = [ln.strip() for ln in master_text.splitlines()]
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            for nxt in lines[i + 1 :]:
                if nxt and not nxt.startswith("#"):
                    return urljoin(base_url, nxt)
    return None


async def probe_hls_drm(fetch_text: Callable[[str], Awaitable[str]], url: str) -> bool:
    """Fetch the master playlist (and first variant if needed) and check for DRM tags."""
    master = await fetch_text(url)
    if is_drm_playlist(master):
        return True
    variant = first_variant_uri(master, url)
    if variant:
        return is_drm_playlist(await fetch_text(variant))
    return False

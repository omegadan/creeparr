"""Synthetic OnlyFans API payloads (shapes verified against a real account; values fake)."""

from __future__ import annotations

from typing import Any

CREATOR_ID = "258702257"


def user() -> dict[str, Any]:
    return {
        "id": 393746487,
        "name": "Test Patron",
        "username": "testpatron",
        "avatar": "https://cdn/av.jpg",
    }


def creator_profile() -> dict[str, Any]:
    return {
        "id": int(CREATOR_ID),
        "name": "Example Model",
        "username": "examplemodel",
        "avatar": "https://cdn/model.jpg",
        "header": "https://cdn/header.jpg",
        "about": "hi",
    }


def _media(
    mid: int, mtype: str = "photo", can_view: bool = True, drm: bool = False
) -> dict[str, Any]:
    files: dict[str, Any] = {
        "full": {"url": f"https://cdn.onlyfans.com/{mid}.{'mp4' if mtype == 'video' else 'jpg'}"}
    }
    if drm:
        files["drm"] = {"manifest": {"dash": "https://cdn/drm.mpd"}}
    return {
        "id": mid,
        "type": mtype,
        "canView": can_view,
        "duration": 10 if mtype == "video" else None,
        "files": files,
    }


def post(
    pid: int, media: list[dict[str, Any]] | None = None, can_view: bool = True
) -> dict[str, Any]:
    return {
        "id": pid,
        "text": f"post {pid}",
        "postedAt": "2026-03-01T12:00:00+00:00",
        "postedAtPrecise": f"{1700000000 + pid}.000000",
        "canViewMedia": can_view,
        "author": {"id": int(CREATOR_ID), "username": "examplemodel"},
        "media": media if media is not None else [_media(pid * 10)],
    }


def posts_page(pids: list[int], has_more: bool = False) -> dict[str, Any]:
    return {"list": [post(p) for p in pids], "hasMore": has_more}


def message(
    mid: int, media: list[dict[str, Any]] | None = None, can_purchase: bool = False
) -> dict[str, Any]:
    return {
        "id": mid,
        "text": f"message {mid}",
        "createdAt": "2026-02-01T10:00:00+00:00",
        "canPurchase": can_purchase,
        "media": media if media is not None else [_media(mid * 100, "video")],
    }


def messages_page(mids: list[int], has_more: bool = False) -> dict[str, Any]:
    return {"list": [message(m) for m in mids], "hasMore": has_more}


VIDEO_DRM = _media(999, "video", drm=True)

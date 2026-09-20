"""Settings and Patreon credentials."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends

from patreonarr.api.deps import get_services
from patreonarr.api.schemas import NamingPreviewBody, PatreonAuthBody
from patreonarr.core.errors import ValidationFailed
from patreonarr.core.naming import render_template
from patreonarr.services import Services

router = APIRouter(tags=["settings"])


async def _apply_side_effects(services: Services, groups: set[str]) -> None:
    if "patreon" in groups:
        await services.patreon.rebuild()
    if "downloads" in groups:
        services.downloads.apply_settings()
    if "scan" in groups:
        services.scheduler.apply_settings()
    services.bus.publish("settings.changed", {"groups": sorted(groups)})


@router.get("/settings")
def get_settings(services: Services = Depends(get_services)) -> dict[str, Any]:
    data = services.settings.masked()
    data["env"] = {
        "config_dir": str(services.env.config_dir),
        "download_dir": str(services.env.download_dir),
        "port": services.env.port,
        "log_level": services.env.log_level,
        "ffmpeg": services.env.resolve_ffmpeg(),
    }
    return data


@router.put("/settings")
async def update_settings(
    patch: dict[str, Any] = Body(...), services: Services = Depends(get_services)
) -> dict[str, Any]:
    patch.pop("env", None)
    if "patreon" in patch:
        patch["patreon"].pop("has_cookies_txt", None)
    services.settings.update(patch, allow_secrets=False)
    await _apply_side_effects(services, set(patch.keys()))
    return get_settings(services)


@router.put("/settings/patreon-auth")
async def set_patreon_auth(body: PatreonAuthBody, services: Services = Depends(get_services)):
    fields: dict[str, Any] = {}
    if body.session_id is not None:
        fields["session_id"] = body.session_id.strip()
    if body.cookies_txt is not None:
        fields["cookies_txt"] = body.cookies_txt
    if not fields:
        raise ValidationFailed("nothing to update")
    services.settings.update({"patreon": fields}, allow_secrets=True)
    await services.patreon.rebuild()
    result = await services.patreon.test_connection()
    return {**result, "settings": services.settings.masked()["patreon"]}


@router.post("/settings/patreon-auth/test")
async def test_patreon_auth(
    body: PatreonAuthBody | None = None, services: Services = Depends(get_services)
):
    if body is None or (body.session_id is None and body.cookies_txt is None):
        return await services.patreon.test_connection()
    return await services.patreon.test_connection(body.session_id or "", body.cookies_txt or "")


@router.delete("/settings/patreon-auth")
async def clear_patreon_auth(services: Services = Depends(get_services)):
    services.settings.update({"patreon": {"session_id": "", "cookies_txt": ""}}, allow_secrets=True)
    await services.patreon.rebuild()
    await services.patreon.check_session()
    return {"ok": True}


@router.post("/settings/naming-preview")
def naming_preview(body: NamingPreviewBody, services: Services = Depends(get_services)):
    max_len = services.settings.get().naming.max_component_length
    sample = {
        "creator": "Example Creator",
        "creator_vanity": "examplecreator",
        "campaign_id": "1234567",
        "title": "Episode 12: Behind the scenes / Q&A",
        "post_id": "98765432",
        "published": datetime(2026, 3, 14, 15, 9, tzinfo=UTC),
        "post_type": "video_external_file",
        "media_kind": "video",
        "media_index": 1,
        "embed_provider": "",
        "filename": "episode-12.mp4",
        "ext": "mp4",
    }
    folder = render_template(body.post_folder_template, sample, max_len)
    file = render_template(body.file_template, sample, max_len)
    return {"post_folder": str(folder), "file": str(file), "full_path": str(folder / file)}

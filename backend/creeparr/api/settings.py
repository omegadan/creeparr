"""Settings and provider credentials."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends

from creeparr.api.deps import get_services
from creeparr.api.schemas import AuthBody, NamingPreviewBody
from creeparr.core.errors import ValidationFailed
from creeparr.core.naming import render_template
from creeparr.services import Services

router = APIRouter(tags=["settings"])


async def _apply_side_effects(services: Services, groups: set[str]) -> None:
    for group in groups:
        if services.providers.has(group):
            await services.providers.get(group).rebuild()
    if "downloads" in groups:
        services.downloads.apply_settings()
    if groups & {"scan", "downloads"}:
        services.scheduler.apply_settings()
    services.bus.publish("settings.changed", {"groups": sorted(groups)})


@router.get("/settings")
def get_settings(services: Services = Depends(get_services)) -> dict[str, Any]:
    data = services.settings.masked()
    data["env"] = {
        **services.env.describe_paths(),
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
    for group in patch.values():
        if isinstance(group, dict):
            for key in [k for k in group if k.startswith("has_")]:
                group.pop(key)
    services.settings.update(patch, allow_secrets=False)
    await _apply_side_effects(services, set(patch.keys()))
    return get_settings(services)


def _credentials(provider, body: AuthBody | None) -> dict[str, str]:  # noqa: ANN001
    if body is None:
        return {}
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    unknown = set(fields) - set(provider.credential_fields)
    if unknown:
        raise ValidationFailed(
            f"unknown credential field(s) for {provider.label}: {sorted(unknown)}"
        )
    return {k: (v.strip() if k != "cookies_txt" else v) for k, v in fields.items()}


@router.put("/settings/auth/{provider_name}")
async def set_auth(provider_name: str, body: AuthBody, services: Services = Depends(get_services)):
    provider = services.providers.get(provider_name)
    fields = _credentials(provider, body)
    if not fields:
        raise ValidationFailed("nothing to update")
    services.settings.update({provider.name: fields}, allow_secrets=True)
    await provider.rebuild()
    result = await provider.test_connection()
    return {**result, "settings": services.settings.masked()[provider.name]}


@router.post("/settings/auth/{provider_name}/test")
async def test_auth(
    provider_name: str, body: AuthBody | None = None, services: Services = Depends(get_services)
):
    provider = services.providers.get(provider_name)
    creds = _credentials(provider, body)
    return await provider.test_connection(creds or None)


@router.delete("/settings/auth/{provider_name}")
async def clear_auth(provider_name: str, services: Services = Depends(get_services)):
    provider = services.providers.get(provider_name)
    services.settings.update(
        {provider.name: dict.fromkeys(provider.credential_fields, "")}, allow_secrets=True
    )
    await provider.rebuild()
    await provider.check_session()
    return {"ok": True}


@router.post("/settings/notifications/test")
async def test_notifications(services: Services = Depends(get_services)):
    return await services.notifications.test()


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

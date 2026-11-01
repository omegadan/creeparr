"""Settings and provider credentials."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import ValidationError

from creeparr.api.deps import get_services
from creeparr.api.schemas import (
    AuthBody,
    AuthTestResultOut,
    NamingPreviewBody,
    NamingPreviewOut,
    NamingTokenOut,
    NotificationTestOut,
    OkOut,
    SamplePathOut,
    SetAuthOut,
    SettingsOut,
)
from creeparr.core.errors import ValidationFailed
from creeparr.core.server_events import SettingsChangedEvent
from creeparr.core.settings_service import NamingSettings, reasons
from creeparr.downloader import paths
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
    services.bus.publish(SettingsChangedEvent(groups=sorted(groups)))


@router.get("/settings", response_model=SettingsOut)
def get_settings(services: Services = Depends(get_services)) -> dict[str, Any]:
    data = services.settings.masked()
    data["env"] = {
        **services.env.describe_paths(p.name for p in services.providers),
        "port": services.env.port,
        "log_level": services.env.log_level,
        "ffmpeg": services.env.resolve_ffmpeg(),
    }
    return data


@router.put("/settings", response_model=SettingsOut)
async def update_settings(
    patch: dict[str, Any] = Body(...), services: Services = Depends(get_services)
):
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
    unknown = set(fields) - set(provider.description.credential_keys)
    if unknown:
        raise ValidationFailed(
            f"unknown credential field(s) for {provider.label}: {sorted(unknown)}"
        )
    return {k: (v.strip() if k != "cookies_txt" else v) for k, v in fields.items()}


@router.put(
    "/settings/auth/{provider_name}", response_model=SetAuthOut, response_model_exclude_unset=True
)
async def set_auth(provider_name: str, body: AuthBody, services: Services = Depends(get_services)):
    provider = services.providers.get(provider_name)
    fields = _credentials(provider, body)
    if not fields:
        raise ValidationFailed("nothing to update")
    services.settings.update({provider.name: fields}, allow_secrets=True)
    await provider.rebuild()
    result = await provider.test_connection()
    return {**result, "settings": services.settings.masked()[provider.name]}


@router.post(
    "/settings/auth/{provider_name}/test",
    response_model=AuthTestResultOut,
    response_model_exclude_unset=True,
)
async def test_auth(
    provider_name: str, body: AuthBody | None = None, services: Services = Depends(get_services)
):
    provider = services.providers.get(provider_name)
    creds = _credentials(provider, body)
    return await provider.test_connection(creds or None)


@router.delete("/settings/auth/{provider_name}", response_model=OkOut)
async def clear_auth(provider_name: str, services: Services = Depends(get_services)):
    provider = services.providers.get(provider_name)
    services.settings.update(
        {provider.name: dict.fromkeys(provider.description.credential_keys, "")}, allow_secrets=True
    )
    await provider.rebuild()
    await provider.check_session()
    return {"ok": True}


@router.post(
    "/settings/notifications/test",
    response_model=NotificationTestOut,
    response_model_exclude_unset=True,
)
async def test_notifications(services: Services = Depends(get_services)):
    return await services.notifications.test()


@router.post("/settings/naming-preview", response_model=NamingPreviewOut)
def naming_preview(body: NamingPreviewBody, services: Services = Depends(get_services)):
    edited = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        naming = NamingSettings.model_validate(
            {**services.settings.get().naming.model_dump(), **edited}
        )
        samples = paths.preview(naming)
    except (ValidationError, ValueError) as exc:
        reason = reasons(exc) if isinstance(exc, ValidationError) else str(exc)
        raise ValidationFailed("invalid template", detail=reason) from exc
    return NamingPreviewOut(
        samples=[SamplePathOut(**vars(sample)) for sample in samples],
        unknown=paths.unknown_tokens(naming),
        tokens=[
            NamingTokenOut(name=t.name, description=t.description, example=example)
            for t, example in paths.token_examples()
        ],
    )

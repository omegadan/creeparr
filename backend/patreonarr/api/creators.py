"""Creators: lookup, add, import pledges, edit, scan, delete."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from patreonarr.api.deps import get_db, get_services
from patreonarr.api.schemas import (
    CampaignPreview,
    CreatorCreate,
    CreatorDefaults,
    CreatorOut,
    CreatorPatch,
    CreatorStats,
    ImportPledgesRequest,
    LookupRequest,
    PledgeOut,
    ScanRequestBody,
    ScanRunOut,
)
from patreonarr.core.errors import Conflict, NotFound, UpstreamError, ValidationFailed
from patreonarr.core.history import record_event
from patreonarr.db.engine import session_scope
from patreonarr.db.enums import EventType, JobStatus, MediaStatus, PostStatus, ScanMode
from patreonarr.db.models import Creator, DownloadJob, MediaItem, Post, ScanRun
from patreonarr.downloader.fs import remove_tree
from patreonarr.downloader.queue import apply_creator_prefs, enqueue_media, recompute_post_status
from patreonarr.patreon.errors import (
    AuthError,
    CloudflareChallengeError,
    NotFoundError,
    PatreonError,
)
from patreonarr.patreon.models import CampaignInfo, PledgeInfo
from patreonarr.scanner.scanner import should_auto_queue
from patreonarr.services import Services

log = logging.getLogger(__name__)
router = APIRouter(tags=["creators"])


# ---- helpers --------------------------------------------------------------------------


def creator_stats(session: Session, creator_ids: list[int]) -> dict[int, CreatorStats]:
    stats = {cid: CreatorStats() for cid in creator_ids}
    if not creator_ids:
        return stats
    rows = session.execute(
        select(Post.creator_id, Post.status, func.count())
        .where(Post.creator_id.in_(creator_ids))
        .group_by(Post.creator_id, Post.status)
    ).all()
    for cid, st, n in rows:
        s = stats[cid]
        s.posts_total += n
        if st == PostStatus.COMPLETED:
            s.posts_completed += n
        elif st == PostStatus.PENDING:
            s.posts_pending += n
        elif st == PostStatus.NO_ACCESS:
            s.posts_no_access += n
        elif st == PostStatus.UNSUPPORTED:
            s.posts_unsupported += n
    rows = session.execute(
        select(MediaItem.creator_id, MediaItem.status, func.count())
        .where(MediaItem.creator_id.in_(creator_ids), MediaItem.wanted.is_(True))
        .group_by(MediaItem.creator_id, MediaItem.status)
    ).all()
    for cid, st, n in rows:
        stats[cid].media_total += n
        if st == MediaStatus.COMPLETED:
            stats[cid].media_completed += n
    rows = session.execute(
        select(DownloadJob.creator_id, func.count())
        .where(
            DownloadJob.creator_id.in_(creator_ids),
            DownloadJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .group_by(DownloadJob.creator_id)
    ).all()
    for cid, n in rows:
        stats[cid].active_jobs = n
    return stats


def to_out(creator: Creator, stats: CreatorStats, scanning: bool) -> CreatorOut:
    out = CreatorOut.model_validate(creator)
    out.stats = stats
    out.scanning = scanning
    return out


def load_creator(session: Session, creator_id: int) -> Creator:
    creator = session.get(Creator, creator_id)
    if creator is None:
        raise NotFound(f"creator {creator_id} not found")
    return creator


def _defaults(services: Services, body: CreatorDefaults) -> dict[str, Any]:
    scan = services.settings.get().scan
    return {
        "monitored": True if body.monitored is None else body.monitored,
        "auto_download": scan.default_auto_download
        if body.auto_download is None
        else body.auto_download,
        "include_images": scan.default_include_images
        if body.include_images is None
        else body.include_images,
        "include_audio": scan.default_include_audio
        if body.include_audio is None
        else body.include_audio,
        "include_attachments": scan.default_include_attachments
        if body.include_attachments is None
        else body.include_attachments,
        "download_since": body.download_since,
    }


def _create_creator(
    services: Services, info: CampaignInfo | PledgeInfo, defaults: dict[str, Any]
) -> int:
    with session_scope(services.session_factory) as s:
        existing = s.execute(
            select(Creator).where(Creator.campaign_id == info.campaign_id)
        ).scalar_one_or_none()
        if existing is not None:
            raise Conflict(f"{existing.name} is already added", code="already_added")
        creator = Creator(
            campaign_id=info.campaign_id,
            vanity=info.vanity,
            name=info.name,
            url=info.url,
            avatar_url=info.avatar_url,
            **defaults,
        )
        if isinstance(info, CampaignInfo):
            creator.cover_url = info.cover_url
            creator.creation_name = info.creation_name
            creator.is_nsfw = info.is_nsfw
            creator.creator_user_id = info.creator_user_id
            creator.raw_json = info.raw
        else:
            creator.pledge_active = True
            creator.pledge_checked_at = datetime.now(UTC)
        s.add(creator)
        s.flush()
        record_event(
            s,
            services.bus,
            EventType.CREATOR_ADDED,
            f"Added creator {creator.name}",
            creator_id=creator.id,
        )
        cid = creator.id
    services.bus.publish("creator.changed", {"id": cid})
    return cid


def _get_out(services: Services, creator_id: int) -> CreatorOut:
    with session_scope(services.session_factory) as s:
        creator = load_creator(s, creator_id)
        stats = creator_stats(s, [creator.id])[creator.id]
        return to_out(creator, stats, services.scan_manager.is_busy(creator.id))


def _upstream(exc: PatreonError) -> UpstreamError:
    return UpstreamError(f"Patreon request failed: {exc}", code=exc.code, detail=exc.detail)


# ---- endpoints ------------------------------------------------------------------------


@router.get("/creators", response_model=list[CreatorOut])
def list_creators(db: Session = Depends(get_db), services: Services = Depends(get_services)):
    creators = db.execute(select(Creator).order_by(func.lower(Creator.name))).scalars().all()
    stats = creator_stats(db, [c.id for c in creators])
    return [to_out(c, stats[c.id], services.scan_manager.is_busy(c.id)) for c in creators]


@router.post("/creators/lookup", response_model=CampaignPreview)
async def lookup_creator(body: LookupRequest, services: Services = Depends(get_services)):
    client = services.patreon.client
    try:
        campaign_id = await client.resolve_campaign_id(body.query)
        info = await client.get_campaign(campaign_id)
    except NotFoundError as exc:
        raise NotFound(str(exc)) from exc
    except (AuthError, CloudflareChallengeError) as exc:
        services.patreon.mark_auth_invalid(exc)
        raise _upstream(exc) from exc
    except PatreonError as exc:
        raise _upstream(exc) from exc

    def _existing() -> int | None:
        with session_scope(services.session_factory) as s:
            return s.execute(
                select(Creator.id).where(Creator.campaign_id == info.campaign_id)
            ).scalar_one_or_none()

    existing = await asyncio.to_thread(_existing)
    return CampaignPreview(
        campaign_id=info.campaign_id,
        name=info.name,
        vanity=info.vanity,
        url=info.url,
        avatar_url=info.avatar_url,
        creation_name=info.creation_name,
        is_nsfw=info.is_nsfw,
        already_added=existing is not None,
        creator_id=existing,
    )


@router.post("/creators", response_model=CreatorOut, status_code=status.HTTP_201_CREATED)
async def add_creator(body: CreatorCreate, services: Services = Depends(get_services)):
    client = services.patreon.client
    try:
        campaign_id = await client.resolve_campaign_id(body.query)
        info = await client.get_campaign(campaign_id)
    except NotFoundError as exc:
        raise NotFound(str(exc)) from exc
    except (AuthError, CloudflareChallengeError) as exc:
        services.patreon.mark_auth_invalid(exc)
        raise _upstream(exc) from exc
    except PatreonError as exc:
        raise _upstream(exc) from exc
    creator_id = await asyncio.to_thread(_create_creator, services, info, _defaults(services, body))
    try:
        services.scan_manager.request_scan(creator_id, ScanMode.FULL, trigger="add")
    except Conflict:
        log.warning("creator added but scan not started: auth invalid")
    return await asyncio.to_thread(_get_out, services, creator_id)


@router.get("/patreon/pledges", response_model=list[PledgeOut])
async def list_pledges(services: Services = Depends(get_services)):
    try:
        pledges = await services.patreon.client.get_pledges()
    except (AuthError, CloudflareChallengeError) as exc:
        services.patreon.mark_auth_invalid(exc)
        raise _upstream(exc) from exc
    except PatreonError as exc:
        raise _upstream(exc) from exc

    def _existing() -> dict[str, int]:
        with session_scope(services.session_factory) as s:
            return dict(s.execute(select(Creator.campaign_id, Creator.id)).all())

    existing = await asyncio.to_thread(_existing)
    return [
        PledgeOut(
            campaign_id=p.campaign_id,
            name=p.name,
            vanity=p.vanity,
            url=p.url,
            avatar_url=p.avatar_url,
            is_free_member=p.is_free_member,
            is_free_trial=p.is_free_trial,
            already_added=p.campaign_id in existing,
            creator_id=existing.get(p.campaign_id),
        )
        for p in pledges
    ]


@router.post("/creators/import-pledges", response_model=list[CreatorOut], status_code=201)
async def import_pledges(body: ImportPledgesRequest, services: Services = Depends(get_services)):
    try:
        pledges = {p.campaign_id: p for p in await services.patreon.client.get_pledges()}
    except (AuthError, CloudflareChallengeError) as exc:
        services.patreon.mark_auth_invalid(exc)
        raise _upstream(exc) from exc
    except PatreonError as exc:
        raise _upstream(exc) from exc
    defaults = _defaults(services, body.defaults)
    created: list[int] = []
    for cid in body.campaign_ids:
        info: PledgeInfo | CampaignInfo | None = pledges.get(cid)
        if info is None:
            try:
                info = await services.patreon.client.get_campaign(cid)
            except PatreonError as exc:
                log.warning("skipping campaign %s: %s", cid, exc)
                continue
        try:
            created.append(await asyncio.to_thread(_create_creator, services, info, defaults))
        except Conflict:
            continue
    for creator_id in created:
        try:
            services.scan_manager.request_scan(creator_id, ScanMode.FULL, trigger="add")
        except Conflict:
            break
    return [await asyncio.to_thread(_get_out, services, cid) for cid in created]


@router.get("/creators/{creator_id}", response_model=CreatorOut)
def get_creator(
    creator_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    creator = load_creator(db, creator_id)
    stats = creator_stats(db, [creator.id])[creator.id]
    return to_out(creator, stats, services.scan_manager.is_busy(creator.id))


@router.patch("/creators/{creator_id}", response_model=CreatorOut)
def patch_creator(
    creator_id: int,
    body: CreatorPatch,
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    creator = load_creator(db, creator_id)
    changes = body.model_dump(exclude_unset=True)
    prefs_changed = False
    for key, value in changes.items():
        if (
            key in ("include_images", "include_audio", "include_attachments")
            and getattr(creator, key) != value
        ):
            prefs_changed = True
        if key == "auto_download" and value and not creator.auto_download:
            prefs_changed = True
        if key == "folder_name" and value is not None:
            value = value.strip() or None
        setattr(creator, key, value)
    queued = 0
    if prefs_changed:
        posts = (
            db.execute(
                select(Post)
                .options(selectinload(Post.media_items))
                .where(Post.creator_id == creator.id)
            )
            .scalars()
            .all()
        )
        for post in posts:
            for item in post.media_items:
                apply_creator_prefs(creator, item)
                if (
                    item.wanted
                    and item.status == MediaStatus.DISCOVERED
                    and should_auto_queue(creator, post)
                    and enqueue_media(db, item)
                ):
                    queued += 1
            recompute_post_status(post)
    db.flush()
    if queued:
        services.downloads.notify()
    services.bus.publish("creator.changed", {"id": creator.id})
    stats = creator_stats(db, [creator.id])[creator.id]
    return to_out(creator, stats, services.scan_manager.is_busy(creator.id))


@router.delete("/creators/{creator_id}", status_code=204)
def delete_creator(
    creator_id: int,
    delete_files: bool = Query(default=False),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    creator = load_creator(db, creator_id)
    jobs = (
        db.execute(
            select(DownloadJob.id).where(
                DownloadJob.creator_id == creator.id, DownloadJob.status == JobStatus.RUNNING
            )
        )
        .scalars()
        .all()
    )
    for jid in jobs:
        services.downloads.cancel_job(jid)
    folders = (
        db.execute(
            select(Post.folder_path).where(
                Post.creator_id == creator.id, Post.folder_path.is_not(None)
            )
        )
        .scalars()
        .all()
    )
    name = creator.name
    db.delete(creator)
    db.flush()
    record_event(db, services.bus, EventType.CREATOR_REMOVED, f"Removed creator {name}")
    if delete_files:
        root = services.env.download_dir
        parents = set()
        for rel in folders:
            path = (root / rel).resolve()
            if root.resolve() in path.parents:
                remove_tree(path)
                parents.add(path.parent)
        for parent in parents:
            try:
                if parent != root.resolve() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
    services.bus.publish("creator.changed", {"id": creator_id, "deleted": True})


@router.post("/creators/{creator_id}/scan", status_code=202)
def scan_creator(
    creator_id: int,
    body: ScanRequestBody | None = None,
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    load_creator(db, creator_id)
    mode_str = (body.mode if body else "auto").lower()
    try:
        mode = ScanMode(mode_str)
    except ValueError as exc:
        raise ValidationFailed(f"invalid scan mode '{mode_str}'") from exc
    queued = services.scan_manager.request_scan(creator_id, mode, trigger="manual")
    return {"queued": queued, "status": services.scan_manager.status()}


@router.post("/creators/scan-all", status_code=202)
def scan_all(services: Services = Depends(get_services)):
    n = services.scan_manager.request_scan_all(ScanMode.AUTO, trigger="manual")
    return {"queued": n}


@router.get("/creators/{creator_id}/scans", response_model=list[ScanRunOut])
def list_scans(
    creator_id: int, limit: int = Query(default=10, le=100), db: Session = Depends(get_db)
):
    load_creator(db, creator_id)
    runs = (
        db.execute(
            select(ScanRun)
            .where(ScanRun.creator_id == creator_id)
            .order_by(ScanRun.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [ScanRunOut.model_validate(r) for r in runs]


@router.post("/creators/{creator_id}/refresh-metadata", response_model=CreatorOut)
async def refresh_metadata(creator_id: int, services: Services = Depends(get_services)):
    def _campaign_id() -> str:
        with session_scope(services.session_factory) as s:
            return load_creator(s, creator_id).campaign_id

    campaign_id = await asyncio.to_thread(_campaign_id)
    try:
        info = await services.patreon.client.get_campaign(campaign_id)
    except PatreonError as exc:
        raise _upstream(exc) from exc

    def _apply() -> None:
        with session_scope(services.session_factory) as s:
            c = load_creator(s, creator_id)
            c.name = info.name
            c.vanity = info.vanity
            c.url = info.url
            c.avatar_url = info.avatar_url
            c.cover_url = info.cover_url
            c.creation_name = info.creation_name
            c.is_nsfw = info.is_nsfw
            c.raw_json = info.raw

    await asyncio.to_thread(_apply)
    services.bus.publish("creator.changed", {"id": creator_id})
    return await asyncio.to_thread(_get_out, services, creator_id)

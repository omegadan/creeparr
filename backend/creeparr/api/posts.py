"""Posts and media items."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from creeparr.api.deps import get_db, get_services
from creeparr.api.schemas import (
    DownloadPostBody,
    MediaItemOut,
    MediaSummary,
    Page,
    PostDetailOut,
    PostOut,
)
from creeparr.core.errors import AppError, NotFound, UpstreamError
from creeparr.db.engine import session_scope
from creeparr.db.enums import JobStatus, MediaStatus, PostStatus
from creeparr.db.models import Creator, MediaItem, Post
from creeparr.downloader.queue import (
    KIND_DISABLED_REASON,
    active_job_for,
    apply_creator_prefs,
    enqueue_media,
    publish_post_changed,
    recompute_post_status,
)
from creeparr.providers.errors import ProviderError
from creeparr.scanner.scanner import sync_post
from creeparr.services import Services

router = APIRouter(tags=["posts"])

PENDING = {MediaStatus.DISCOVERED, MediaStatus.QUEUED, MediaStatus.DOWNLOADING}
FAILED = {
    MediaStatus.FAILED,
    MediaStatus.FAILED_PERMANENT,
    MediaStatus.CANCELLED,
    MediaStatus.MISSING,
}
UNSUPPORTED = {MediaStatus.UNSUPPORTED, MediaStatus.UNSUPPORTED_DRM}


def summarise(post: Post) -> MediaSummary:
    ms = MediaSummary()
    for m in post.media_items:
        if (
            not m.wanted
            and m.status == MediaStatus.SKIPPED
            and m.status_reason == KIND_DISABLED_REASON
        ):
            continue
        ms.total += 1
        if m.status == MediaStatus.COMPLETED:
            ms.completed += 1
        elif m.status in PENDING:
            ms.pending += 1
        elif m.status in FAILED:
            ms.failed += 1
        elif m.status in UNSUPPORTED:
            ms.unsupported += 1
        elif m.status == MediaStatus.SKIPPED:
            ms.skipped += 1
    return ms


def post_out(post: Post, creator_name: str | None = None) -> PostOut:
    out = PostOut.model_validate(post)
    out.creator_name = creator_name
    out.media_summary = summarise(post)
    return out


def post_detail(post: Post, creator_name: str | None = None) -> PostDetailOut:
    out = PostDetailOut.model_validate(post)
    out.creator_name = creator_name
    out.media_summary = summarise(post)
    out.media_items = [MediaItemOut.model_validate(m) for m in post.media_items]
    return out


def load_post(session: Session, post_id: int) -> Post:
    post = session.execute(
        select(Post)
        .options(selectinload(Post.media_items), selectinload(Post.creator))
        .where(Post.id == post_id)
    ).scalar_one_or_none()
    if post is None:
        raise NotFound(f"post {post_id} not found")
    return post


def load_media(session: Session, media_id: int) -> MediaItem:
    item = session.execute(
        select(MediaItem)
        .options(selectinload(MediaItem.post).selectinload(Post.media_items))
        .where(MediaItem.id == media_id)
    ).scalar_one_or_none()
    if item is None:
        raise NotFound(f"media item {media_id} not found")
    return item


@router.get("/posts", response_model=Page[PostOut])
def list_posts(
    creator_id: int | None = None,
    status: str | None = Query(default=None, description="comma-separated post statuses"),
    post_type: str | None = None,
    q: str | None = None,
    published_after: datetime | None = None,
    published_before: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    sort: str = Query(default="-published_at"),
    db: Session = Depends(get_db),
):
    stmt = select(Post).options(selectinload(Post.media_items), selectinload(Post.creator))
    if creator_id is not None:
        stmt = stmt.where(Post.creator_id == creator_id)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        stmt = stmt.where(Post.status.in_(statuses))
    if post_type:
        stmt = stmt.where(Post.post_type == post_type)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Post.title.ilike(like), Post.teaser_text.ilike(like)))
    if published_after:
        stmt = stmt.where(Post.published_at >= published_after)
    if published_before:
        stmt = stmt.where(Post.published_at <= published_before)

    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()

    desc = sort.startswith("-")
    key = sort.lstrip("-+")
    column = {
        "published_at": Post.published_at,
        "title": Post.title,
        "status": Post.status,
        "first_seen_at": Post.first_seen_at,
    }.get(key, Post.published_at)
    stmt = stmt.order_by(
        column.desc().nulls_last() if desc else column.asc().nulls_last(), Post.id.desc()
    )
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    posts = db.execute(stmt).scalars().all()
    return Page(
        items=[post_out(p, p.creator.name if p.creator else None) for p in posts],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/posts/{post_id}", response_model=PostDetailOut)
def get_post(post_id: int, db: Session = Depends(get_db)):
    post = load_post(db, post_id)
    return post_detail(post, post.creator.name if post.creator else None)


@router.get("/posts/{post_id}/raw")
def get_post_raw(post_id: int, db: Session = Depends(get_db)):
    return load_post(db, post_id).raw_json or {}


@router.post("/posts/{post_id}/download", response_model=PostDetailOut)
def download_post(
    post_id: int,
    body: DownloadPostBody | None = None,
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    force = bool(body and body.force)
    post = load_post(db, post_id)
    if post.status == PostStatus.SKIPPED:
        post.status = PostStatus.NEW
    n = 0
    for item in post.media_items:
        if item.status == MediaStatus.SKIPPED and item.status_reason != KIND_DISABLED_REASON:
            item.status = MediaStatus.DISCOVERED
            item.status_reason = None
            item.wanted = True
        if force and item.status in (
            MediaStatus.COMPLETED,
            MediaStatus.FAILED_PERMANENT,
            *UNSUPPORTED,
        ):
            item.attempts = 0
        if enqueue_media(
            db, item, priority=10, force=force and item.status != MediaStatus.NO_ACCESS
        ):
            n += 1
    recompute_post_status(post)
    db.flush()
    if n:
        services.downloads.notify()
    publish_post_changed(services.bus, post)
    return post_detail(post, post.creator.name if post.creator else None)


@router.post("/posts/{post_id}/skip", response_model=PostDetailOut)
def skip_post(
    post_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    post = load_post(db, post_id)
    for item in post.media_items:
        job = active_job_for(db, item.id)
        if job is not None:
            if job.status == JobStatus.RUNNING:
                services.downloads.cancel_job(job.id)
            else:
                job.status = JobStatus.CANCELLED
        if item.status != MediaStatus.COMPLETED:
            item.status = MediaStatus.SKIPPED
            item.status_reason = "skipped by user"
            item.wanted = False
    post.status = PostStatus.SKIPPED
    db.flush()
    publish_post_changed(services.bus, post)
    return post_detail(post, post.creator.name if post.creator else None)


@router.post("/posts/{post_id}/unskip", response_model=PostDetailOut)
def unskip_post(
    post_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    post = load_post(db, post_id)
    creator = post.creator
    for item in post.media_items:
        if item.status == MediaStatus.SKIPPED:
            item.status = MediaStatus.DISCOVERED
            item.status_reason = None
            item.wanted = True
            apply_creator_prefs(creator, item)
    post.status = PostStatus.NEW
    recompute_post_status(post)
    db.flush()
    publish_post_changed(services.bus, post)
    return post_detail(post, creator.name if creator else None)


@router.post("/posts/{post_id}/refresh", response_model=PostDetailOut)
async def refresh_post(post_id: int, services: Services = Depends(get_services)):
    def _ext_id() -> tuple[str, int, str, str]:
        with session_scope(services.session_factory) as s:
            post = load_post(s, post_id)
            return post.post_id, post.creator_id, post.creator.provider, post.creator.campaign_id

    ext_id, creator_id, provider_name, external_id = await asyncio.to_thread(_ext_id)
    provider = services.providers.get(provider_name)
    try:
        pr = await provider.get_post(external_id, ext_id)
    except ProviderError as exc:
        raise UpstreamError(f"{provider.label} request failed: {exc}", code=exc.code) from exc

    def _apply() -> PostDetailOut:
        with session_scope(services.session_factory) as s:
            creator = s.get(Creator, creator_id)
            post, _, _, queued = sync_post(s, creator, pr, provider.resolve_media)
            s.flush()
            publish_post_changed(services.bus, post)
            if queued:
                services.downloads.notify()
            return post_detail(load_post(s, post.id), creator.name if creator else None)

    return await asyncio.to_thread(_apply)


# ---- media ----------------------------------------------------------------------------


@router.post("/media/{media_id}/retry", response_model=MediaItemOut)
def retry_media(
    media_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    item = load_media(db, media_id)
    if item.status in (MediaStatus.FAILED_PERMANENT, MediaStatus.MISSING, *UNSUPPORTED):
        item.attempts = 0
    if item.status == MediaStatus.SKIPPED:
        item.status = MediaStatus.DISCOVERED
        item.status_reason = None
    item.wanted = True
    enqueue_media(db, item, priority=10, force=item.status != MediaStatus.NO_ACCESS)
    recompute_post_status(item.post)
    db.flush()
    services.downloads.notify()
    publish_post_changed(services.bus, item.post)
    return MediaItemOut.model_validate(item)


@router.post("/media/{media_id}/skip", response_model=MediaItemOut)
def skip_media(
    media_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    item = load_media(db, media_id)
    job = active_job_for(db, item.id)
    if job is not None:
        if job.status == JobStatus.RUNNING:
            services.downloads.cancel_job(job.id)
        else:
            job.status = JobStatus.CANCELLED
    if item.status != MediaStatus.COMPLETED:
        item.status = MediaStatus.SKIPPED
        item.status_reason = "skipped by user"
    item.wanted = False
    recompute_post_status(item.post)
    db.flush()
    publish_post_changed(services.bus, item.post)
    return MediaItemOut.model_validate(item)


@router.post("/media/{media_id}/unskip", response_model=MediaItemOut)
def unskip_media(
    media_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    item = load_media(db, media_id)
    if item.status == MediaStatus.SKIPPED:
        item.status = MediaStatus.DISCOVERED
        item.status_reason = None
    item.wanted = True
    recompute_post_status(item.post)
    db.flush()
    publish_post_changed(services.bus, item.post)
    return MediaItemOut.model_validate(item)


@router.get("/media/{media_id}/file")
def media_file(
    media_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    item = load_media(db, media_id)
    if item.status != MediaStatus.COMPLETED or not item.file_path:
        raise NotFound("no downloaded file for this media item")
    provider = item.post.creator.provider if item.post and item.post.creator else "patreon"
    root = services.env.download_root(provider).resolve()
    path = (root / item.file_path).resolve()
    if root not in path.parents:
        raise AppError("invalid path", code="forbidden")
    if not path.exists():
        raise NotFound("file is missing on disk")
    return FileResponse(path, filename=path.name)

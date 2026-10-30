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
from creeparr.core.lifecycle import KIND_DISABLED_REASON
from creeparr.db.engine import session_scope
from creeparr.db.enums import FAILED_MEDIA_STATUSES, MediaStatus
from creeparr.db.models import Creator, MediaItem, Post
from creeparr.providers.errors import ProviderError
from creeparr.services import Services

router = APIRouter(tags=["posts"])

PENDING = {MediaStatus.DISCOVERED, MediaStatus.QUEUED, MediaStatus.DOWNLOADING}
UNSUPPORTED = {MediaStatus.UNSUPPORTED, MediaStatus.UNSUPPORTED_DRM}
# Cancelled media was stopped by the user, so it counts with skipped, not failed.
SKIPPED = {MediaStatus.SKIPPED, MediaStatus.CANCELLED}


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
        elif m.status in FAILED_MEDIA_STATUSES:
            ms.failed += 1
        elif m.status in UNSUPPORTED:
            ms.unsupported += 1
        elif m.status in SKIPPED:
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
    post = load_post(db, post_id)
    services.lifecycle.download_post(db, post, force=bool(body and body.force))
    return post_detail(post, post.creator.name if post.creator else None)


@router.post("/posts/{post_id}/skip", response_model=PostDetailOut)
def skip_post(
    post_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    post = load_post(db, post_id)
    services.lifecycle.skip_post(db, post)
    return post_detail(post, post.creator.name if post.creator else None)


@router.post("/posts/{post_id}/unskip", response_model=PostDetailOut)
def unskip_post(
    post_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    post = load_post(db, post_id)
    services.lifecycle.unskip_post(db, post)
    return post_detail(post, post.creator.name if post.creator else None)


@router.post("/posts/{post_id}/refresh", response_model=PostDetailOut)
async def refresh_post(post_id: int, services: Services = Depends(get_services)):
    def _ext_id() -> tuple[str, str | None, int, str, str]:
        with session_scope(services.session_factory) as s:
            post = load_post(s, post_id)
            c = post.creator
            return post.post_id, post.post_type, post.creator_id, c.provider, c.campaign_id

    ext_id, post_type, creator_id, provider_name, external_id = await asyncio.to_thread(_ext_id)
    provider = services.providers.get(provider_name)
    try:
        pr = await provider.get_post(external_id, ext_id, post_type=post_type)
    except ProviderError as exc:
        raise UpstreamError(f"{provider.label} request failed: {exc}", code=exc.code) from exc

    def _apply() -> PostDetailOut:
        with session_scope(services.session_factory) as s:
            creator = s.get(Creator, creator_id)
            result = services.lifecycle.sync_post(
                s, creator, pr, provider.resolve_media, refresh=True
            )
            return post_detail(load_post(s, result.post.id), creator.name if creator else None)

    return await asyncio.to_thread(_apply)


# ---- media ----------------------------------------------------------------------------


@router.post("/media/{media_id}/retry", response_model=MediaItemOut)
def retry_media(
    media_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    item = load_media(db, media_id)
    services.lifecycle.retry_media(db, item)
    return MediaItemOut.model_validate(item)


@router.post("/media/{media_id}/skip", response_model=MediaItemOut)
def skip_media(
    media_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    item = load_media(db, media_id)
    services.lifecycle.skip_media(db, item)
    return MediaItemOut.model_validate(item)


@router.post("/media/{media_id}/unskip", response_model=MediaItemOut)
def unskip_media(
    media_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    item = load_media(db, media_id)
    services.lifecycle.unskip_media(db, item)
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

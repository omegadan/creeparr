"""Download queue."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from patrearr.api.deps import get_db, get_services
from patrearr.api.schemas import FailedMediaOut, JobOut, QueueOut
from patrearr.core.errors import NotFound
from patrearr.db.enums import JobStatus, MediaStatus
from patrearr.db.models import DownloadJob, MediaItem, Post
from patrearr.downloader.queue import enqueue_media, recompute_post_status
from patrearr.services import Services

router = APIRouter(tags=["queue"])

FAILED_STATUSES = [MediaStatus.FAILED, MediaStatus.FAILED_PERMANENT]


def job_out(job: DownloadJob) -> JobOut:
    media = job.media_item
    post = media.post if media else None
    creator = post.creator if post else None
    return JobOut(
        id=job.id,
        media_item_id=job.media_item_id,
        post_id=job.post_id,
        creator_id=job.creator_id,
        creator_name=creator.name if creator else None,
        post_title=post.title if post else None,
        media_kind=media.kind if media else None,
        source=media.source if media else None,
        file_name=(media.remote_file_name or (post.title if post else None)) if media else None,
        status=job.status,
        priority=job.priority,
        attempt=job.attempt,
        progress_percent=job.progress_percent,
        bytes_downloaded=job.bytes_downloaded,
        total_bytes=job.total_bytes,
        speed_bps=job.speed_bps,
        eta_seconds=job.eta_seconds,
        stage=job.stage,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        error_class=job.error_class,
        created_at=job.created_at,
    )


def failed_out(m: MediaItem) -> FailedMediaOut:
    post = m.post
    creator = post.creator if post else None
    return FailedMediaOut(
        media_item_id=m.id,
        post_id=m.post_id,
        creator_id=m.creator_id,
        creator_name=creator.name if creator else None,
        post_title=post.title if post else None,
        media_kind=m.kind,
        source=m.source,
        status=m.status,
        status_reason=m.status_reason,
        attempts=m.attempts,
        next_retry_at=m.next_retry_at,
        last_error=m.last_error,
    )


def _job_query():
    return select(DownloadJob).options(
        selectinload(DownloadJob.media_item).selectinload(MediaItem.post).selectinload(Post.creator)
    )


@router.get("/queue", response_model=QueueOut)
def get_queue(
    limit: int = Query(default=200, le=1000),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    jobs = (
        db.execute(
            _job_query()
            .where(DownloadJob.status.in_([JobStatus.RUNNING, JobStatus.QUEUED]))
            .order_by(
                (DownloadJob.status == JobStatus.RUNNING).desc(),
                DownloadJob.priority.desc(),
                DownloadJob.created_at,
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    failed = (
        db.execute(
            select(MediaItem)
            .options(selectinload(MediaItem.post).selectinload(Post.creator))
            .where(MediaItem.status.in_(FAILED_STATUSES))
            .order_by(MediaItem.updated_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    st = services.downloads.status()
    return QueueOut(
        paused=st["paused"],
        paused_reason=st["paused_reason"],
        jobs=[job_out(j) for j in jobs],
        failed=[failed_out(m) for m in failed],
        providers=services.downloads.provider_status(),
    )


@router.delete("/queue/{job_id}", status_code=204)
def remove_job(
    job_id: int,
    skip_media: bool = Query(default=False),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    job = db.execute(_job_query().where(DownloadJob.id == job_id)).scalar_one_or_none()
    if job is None:
        raise NotFound(f"job {job_id} not found")
    media = job.media_item
    if job.status == JobStatus.RUNNING:
        services.downloads.cancel_job(job.id)
        if skip_media and media is not None:
            media.wanted = False
    elif job.status == JobStatus.QUEUED:
        job.status = JobStatus.CANCELLED
        if media is not None:
            media.status = MediaStatus.SKIPPED if skip_media else MediaStatus.CANCELLED
            media.status_reason = "removed from queue"
            if skip_media:
                media.wanted = False
            if media.post is not None:
                recompute_post_status(media.post)
    db.flush()
    services.bus.publish("queue.changed", {})


@router.post("/queue/{job_id}/retry", status_code=202)
def retry_job(
    job_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    job = db.execute(_job_query().where(DownloadJob.id == job_id)).scalar_one_or_none()
    if job is None:
        raise NotFound(f"job {job_id} not found")
    media = job.media_item
    if media is None:
        raise NotFound("media item no longer exists")
    if media.status == MediaStatus.FAILED_PERMANENT:
        media.attempts = 0
    new = enqueue_media(db, media, priority=10, force=media.status != MediaStatus.NO_ACCESS)
    if media.post is not None:
        recompute_post_status(media.post)
    db.flush()
    services.downloads.notify()
    services.bus.publish("queue.changed", {})
    return {"job_id": new.id if new else None}


@router.post("/queue/pause")
def pause_queue(services: Services = Depends(get_services)):
    services.downloads.pause("user")
    return services.downloads.status()


@router.post("/queue/resume")
def resume_queue(services: Services = Depends(get_services)):
    services.downloads.resume()
    return services.downloads.status()


@router.post("/queue/retry-failed")
def retry_failed(db: Session = Depends(get_db), services: Services = Depends(get_services)):
    items = (
        db.execute(
            select(MediaItem)
            .options(selectinload(MediaItem.post).selectinload(Post.media_items))
            .where(MediaItem.status.in_(FAILED_STATUSES))
        )
        .scalars()
        .all()
    )
    n = 0
    for m in items:
        if m.status == MediaStatus.FAILED_PERMANENT:
            m.attempts = 0
        if enqueue_media(db, m, force=True):
            n += 1
        if m.post is not None:
            recompute_post_status(m.post)
    db.flush()
    services.downloads.notify()
    services.bus.publish("queue.changed", {})
    return {"requeued": n}


@router.delete("/queue/failed")
def clear_failed(db: Session = Depends(get_db), services: Services = Depends(get_services)):
    items = (
        db.execute(
            select(MediaItem)
            .options(selectinload(MediaItem.post).selectinload(Post.media_items))
            .where(MediaItem.status.in_(FAILED_STATUSES))
        )
        .scalars()
        .all()
    )
    for m in items:
        m.status = MediaStatus.SKIPPED
        m.status_reason = "cleared from failed list"
        m.wanted = False
        if m.post is not None:
            recompute_post_status(m.post)
    db.flush()
    services.bus.publish("queue.changed", {})
    return {"cleared": len(items)}

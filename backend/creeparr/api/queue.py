"""Download queue."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from creeparr.api.deps import get_db, get_services
from creeparr.api.schemas import (
    ClearedOut,
    DownloadsStatusOut,
    FailedMediaOut,
    JobOut,
    JobRetriedOut,
    QueueOut,
    RequeuedOut,
)
from creeparr.core.errors import NotFound
from creeparr.db.enums import FAILED_MEDIA_STATUSES, JobStatus
from creeparr.db.models import DownloadJob, MediaItem, Post
from creeparr.services import Services

router = APIRouter(tags=["queue"])


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
    page: int = Query(default=1, ge=1),
    failed_page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    """One page of the queue (running first, then in download order) and of failures.

    The queue itself is unbounded; only what is sent to the browser is paged. The
    totals always count everything.
    """
    job_counts = dict(
        db.execute(
            select(DownloadJob.status, func.count())
            .where(DownloadJob.status.in_([JobStatus.RUNNING, JobStatus.QUEUED]))
            .group_by(DownloadJob.status)
        ).all()
    )
    failed_total = db.execute(
        select(func.count())
        .select_from(MediaItem)
        .where(MediaItem.status.in_(FAILED_MEDIA_STATUSES))
    ).scalar_one()
    jobs = (
        db.execute(
            _job_query()
            .where(DownloadJob.status.in_([JobStatus.RUNNING, JobStatus.QUEUED]))
            .order_by(
                (DownloadJob.status == JobStatus.RUNNING).desc(),
                DownloadJob.priority.desc(),
                DownloadJob.created_at,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    failed = (
        db.execute(
            select(MediaItem)
            .options(selectinload(MediaItem.post).selectinload(Post.creator))
            .where(MediaItem.status.in_(FAILED_MEDIA_STATUSES))
            .order_by(MediaItem.updated_at.desc())
            .offset((failed_page - 1) * page_size)
            .limit(page_size)
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
        running_total=job_counts.get(JobStatus.RUNNING, 0),
        queued_total=job_counts.get(JobStatus.QUEUED, 0),
        failed_total=failed_total,
        page=page,
        failed_page=failed_page,
        page_size=page_size,
    )


@router.delete("/queue/{job_id:int}", status_code=204)
def remove_job(
    job_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    job = db.execute(_job_query().where(DownloadJob.id == job_id)).scalar_one_or_none()
    if job is None:
        raise NotFound(f"job {job_id} not found")
    services.lifecycle.remove_job(db, job)


@router.post("/queue/{job_id:int}/retry", status_code=202, response_model=JobRetriedOut)
def retry_job(
    job_id: int, db: Session = Depends(get_db), services: Services = Depends(get_services)
):
    job = db.execute(_job_query().where(DownloadJob.id == job_id)).scalar_one_or_none()
    if job is None:
        raise NotFound(f"job {job_id} not found")
    if job.media_item is None:
        raise NotFound("media item no longer exists")
    new = services.lifecycle.retry_media(db, job.media_item)
    return {"job_id": new.id if new else None}


@router.post("/queue/pause", response_model=DownloadsStatusOut)
def pause_queue(services: Services = Depends(get_services)):
    services.downloads.pause("user")
    return services.downloads.status()


@router.post("/queue/resume", response_model=DownloadsStatusOut)
def resume_queue(services: Services = Depends(get_services)):
    services.downloads.resume()
    return services.downloads.status()


@router.post("/queue/retry-failed", response_model=RequeuedOut)
def retry_failed(db: Session = Depends(get_db), services: Services = Depends(get_services)):
    return {"requeued": services.lifecycle.retry_failed(db)}


@router.delete("/queue/failed", response_model=ClearedOut)
def clear_failed(db: Session = Depends(get_db), services: Services = Depends(get_services)):
    return {"cleared": services.lifecycle.clear_failed(db)}

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from patreonarr.api.deps import get_db
from patreonarr.api.schemas import HistoryOut, Page
from patreonarr.db.models import Creator, History, Post

router = APIRouter(tags=["history"])


@router.get("/history", response_model=Page[HistoryOut])
def list_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    event_type: str | None = None,
    creator_id: int | None = None,
    level: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = (
        select(History, Creator.name, Post.title)
        .outerjoin(Creator, Creator.id == History.creator_id)
        .outerjoin(Post, Post.id == History.post_id)
    )
    if event_type:
        stmt = stmt.where(History.event_type.in_([e.strip() for e in event_type.split(",")]))
    if creator_id is not None:
        stmt = stmt.where(History.creator_id == creator_id)
    if level:
        stmt = stmt.where(History.level.in_([lv.strip() for lv in level.split(",")]))
    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(
        stmt.order_by(History.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [
        HistoryOut(
            id=h.id,
            occurred_at=h.occurred_at,
            event_type=h.event_type,
            level=h.level,
            creator_id=h.creator_id,
            creator_name=cname,
            post_id=h.post_id,
            post_title=ptitle,
            media_item_id=h.media_item_id,
            message=h.message,
            data=h.data,
        )
        for h, cname, ptitle in rows
    ]
    return Page(items=items, total=total, page=page, page_size=page_size)

from __future__ import annotations

from fastapi import APIRouter

from patreonarr.api import creators, history, posts, queue, settings, system

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(creators.router)
api_router.include_router(posts.router)
api_router.include_router(queue.router)
api_router.include_router(history.router)
api_router.include_router(settings.router)

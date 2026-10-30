"""The embed step: a post's title, date, description and cover, written into its video.

Downloads use it on each new video; the embed backlog task on archived videos that lack it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from creeparr.core.settings_service import SettingsService
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.models import MediaItem, Post
from creeparr.downloader.fs import sha256_file
from creeparr.downloader.handlers.base import ProgressReporter
from creeparr.downloader.longwork import LongWork
from creeparr.downloader.metadata import Ffmpeg, html_to_text
from creeparr.providers.media import MediaSession
from creeparr.providers.registry import ProviderRegistry

log = logging.getLogger(__name__)

_COVER_MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Embedded:
    """The video after embedding: it was rewritten, so its size and hash changed."""

    size: int
    sha256: str | None


@dataclass(frozen=True)
class _Tags:
    provider: str
    metadata: dict[str, str]
    cover_url: str | None


class Embedder:
    def __init__(
        self,
        session_factory: SessionFactory,
        settings: SettingsService,
        providers: ProviderRegistry,
        ffmpeg: Ffmpeg,
        long_work: LongWork,
    ) -> None:
        self._factory = session_factory
        self._settings = settings
        self._providers = providers
        self._ffmpeg = ffmpeg
        self._long = long_work

    @property
    def available(self) -> bool:
        return self._ffmpeg.location is not None

    async def embed(
        self, media_id: int, video: Path, progress: ProgressReporter | None = None
    ) -> Embedded | None:
        """Embed the media's post, as it is in the database now, into `video` in place.

        None when ffmpeg is missing or fails: the file is untouched and stays in the backlog.
        """
        if not self.available:
            return None
        if progress is not None:
            progress.set_stage("embedding")
        tags = await asyncio.to_thread(self._load, media_id)
        if tags is None:
            return None
        cover: Path | None = None
        if tags.cover_url:
            cover = await _fetch_cover(
                self._providers.get(tags.provider).media_session(),
                tags.cover_url,
                video.with_name(f".creeparr-cover-{media_id}.jpg"),
            )

        def _do() -> Embedded | None:
            changed = self._ffmpeg.embed(video, tags.metadata, cover)
            if cover is not None:
                cover.unlink(missing_ok=True)
            if not changed:
                return None
            size = video.stat().st_size
            sha = sha256_file(video) if self._settings.get().downloads.compute_sha256 else None
            return Embedded(size, sha)

        return await self._long.run(_do)

    def _load(self, media_id: int) -> _Tags | None:
        with session_scope(self._factory) as s:
            media = s.execute(
                select(MediaItem)
                .options(selectinload(MediaItem.post).selectinload(Post.creator))
                .where(MediaItem.id == media_id)
            ).scalar_one_or_none()
            if media is None or media.post is None:
                return None
            post, creator = media.post, media.post.creator
            name = creator.name if creator else ""
            description = html_to_text(post.content_html or post.teaser_text)
            return _Tags(
                provider=creator.provider if creator else "patreon",
                metadata={
                    "title": post.title or "",
                    "artist": name,
                    "album_artist": name,
                    "comment": description,
                    "description": description,
                    "date": post.published_at.strftime("%Y-%m-%d") if post.published_at else "",
                },
                cover_url=post.thumbnail_url,
            )


async def _fetch_cover(session: MediaSession, url: str, dest: Path) -> Path | None:
    try:
        resp = await session.stream(url)
        data = bytearray()
        async for chunk in resp.aiter_bytes(1 << 16):
            data.extend(chunk)
            if len(data) > _COVER_MAX_BYTES:
                break
        await resp.aclose()
        if not data:
            return None
        dest.write_bytes(bytes(data))
        return dest
    except Exception as exc:  # noqa: BLE001
        log.debug("thumbnail fetch failed: %s", exc)
        return None

"""Where a download's file lands in the library.

A file goes into its post's folder, under a name no other running download holds. A download
that only learns an undated post's date at the end (yt-dlp) works in a scratch folder, and
its file is filed once the date, and so the folder, is known.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from creeparr.core.naming import unique_path
from creeparr.core.settings_service import NamingSettings
from creeparr.db.engine import SessionFactory, session_scope
from creeparr.db.models import Creator, Post
from creeparr.downloader.fs import atomic_replace, remove_tree
from creeparr.downloader.handlers.base import Learned, MediaSnapshot
from creeparr.downloader.paths import file_name, post_folder
from creeparr.downloader.sidecars import write_text_sidecars

log = logging.getLogger(__name__)

TMP_PREFIX = ".creeparr-tmp-"


class Reservations:
    """File names that running downloads have picked but not finished writing, so two
    same-named files of one post downloading at once get distinct names."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: dict[int, set[Path]] = {}

    def reserve(self, job_id: int, path: Path) -> Path:
        """`path`, or the first free variant of it, now held for `job_id`."""
        with self._lock:
            taken = set().union(*self._held.values())
            dest = unique_path(path, taken)
            self._held.setdefault(job_id, set()).add(dest)
            return dest

    def release(self, job_id: int) -> None:
        with self._lock:
            self._held.pop(job_id, None)


class PostPlacement:
    """One download's Placement (see `handlers.base`).

    The post's folder is committed once: created, recorded on the post and given its
    sidecars. That happens up front, unless the date is pending; then it happens when the
    run files the scratch download.
    """

    def __init__(
        self,
        *,
        root: Path,
        media: MediaSnapshot,
        naming: NamingSettings,
        reservations: Reservations,
        session_factory: SessionFactory,
        date_pending: bool,
    ) -> None:
        self._root = root
        self._media = media
        self._naming = naming
        self._reservations = reservations
        self._factory = session_factory
        self.date_pending = date_pending
        # Named after the folder as first decided; filing may move the file elsewhere.
        self._scratch = root / post_folder(naming, media) / f"{TMP_PREFIX}{media.media_id}"

    @property
    def media(self) -> MediaSnapshot:
        """The download's media, with the post's date once it has been learned."""
        return self._media

    def reserve(self, ext: str) -> Path:
        if self.date_pending:
            raise RuntimeError("the post's date isn't known yet; download into scratch()")
        name = file_name(self._naming, self._media, ext)
        return self._reservations.reserve(self._media.job_id, self._folder() / name)

    def scratch(self) -> Path:
        self._scratch.mkdir(parents=True, exist_ok=True)
        return self._scratch

    def commit_folder(self) -> None:
        """Create the post's folder, record it on the post and write the post's sidecars
        (once per post). Blocking."""
        folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        if not self._naming.write_sidecars:
            return
        with session_scope(self._factory) as s:
            post = s.get(Post, self._media.post_id)
            creator = s.get(Creator, self._media.creator_id)
            if post is None or creator is None:
                return
            rel = str(folder.relative_to(self._root))
            if post.folder_path != rel:
                post.folder_path = rel
            if post.sidecars_written:
                return
            try:
                write_text_sidecars(folder, post, creator)
                post.sidecars_written = True
            except OSError as exc:
                log.warning("could not write sidecars for post %s: %s", post.post_id, exc)

    def file(self, produced: Path, learned: Learned | None) -> Path:
        """Move a scratch download into the post's folder. With the date pending, what was
        learned is saved to the post first, then the folder is committed. Blocking."""
        if self.date_pending:
            if learned is not None:
                self._learn(learned)
            self.date_pending = False
            self.commit_folder()
        dest = self.reserve(produced.suffix.lstrip(".").lower() or "mp4")
        atomic_replace(produced, dest)
        return dest

    def remove_scratch(self) -> None:
        """Remove the scratch folder, and the folder holding it if that is left empty (one
        made before a learned date moved the file elsewhere)."""
        remove_tree(self._scratch)
        with contextlib.suppress(OSError):
            self._scratch.parent.rmdir()  # fails, as intended, unless empty

    def _folder(self) -> Path:
        return self._root / post_folder(self._naming, self._media)

    def _learn(self, learned: Learned) -> None:
        """Save a learned publish date (and a description the post lacks) to the post."""
        published = learned.published_at
        if self._media.published_at is not None or published is None:
            return
        self._media = replace(self._media, published_at=published)
        with session_scope(self._factory) as s:
            post = s.get(Post, self._media.post_id)
            if post is not None and post.published_at is None:
                post.published_at = published
                if not post.content_html and learned.description:
                    post.content_html = learned.description


def sweep_scratch(roots: Iterable[Path]) -> None:
    """Delete scratch folders that interrupted downloads left behind."""
    for root in roots:
        if not root.exists():
            continue
        try:
            for p in root.rglob(f"{TMP_PREFIX}*"):
                if p.is_dir():
                    remove_tree(p)
        except OSError as exc:
            log.debug("temp sweep failed for %s: %s", root, exc)

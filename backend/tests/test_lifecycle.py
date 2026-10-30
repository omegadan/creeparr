"""The media lifecycle, tested through its interface: actions, syncs, downloads and reconcile."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import ANY

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from creeparr.core.downloads import Cancelled, Claim, Completed, Failed, FailureKind, Outcome
from creeparr.core.lifecycle import (
    CANCELLED_REASON,
    DRM_REASON,
    KIND_DISABLED_REASON,
    RULES_KEY,
    RULES_VERSION,
    USER_SKIP_REASON,
    MediaLifecycle,
    derive_post_status,
)
from creeparr.core.state import get_state
from creeparr.db.engine import session_scope
from creeparr.db.enums import EventType, JobStatus, MediaKind, MediaStatus, PostStatus
from creeparr.db.models import Creator, DownloadJob, History, MediaItem, Post
from creeparr.patreon.media_resolver import resolve_media
from creeparr.patreon.parsing import IncludedIndex, post_from_resource
from tests import patreon_fixtures as fx

VIDEO, IMAGE = MediaKind.VIDEO, MediaKind.IMAGE
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
_campaign_ids = itertools.count(1)


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event, data or {}))


class FakeWorkers:
    def __init__(self) -> None:
        self.cancelled: list[int] = []
        self.notified = 0

    def notify(self) -> None:
        self.notified += 1

    def cancel_job(self, job_id: int) -> bool:
        self.cancelled.append(job_id)
        return True


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


@pytest.fixture
def workers() -> FakeWorkers:
    return FakeWorkers()


@pytest.fixture
def lifecycle(session_factory, bus, settings, workers) -> MediaLifecycle:
    return MediaLifecycle(session_factory, bus, settings, workers)


@dataclass
class M:
    """A media item as seeded, with the active job it has, if any."""

    status: str
    kind: str = VIDEO
    wanted: bool = True
    reason: str | None = None
    job: str | None = None
    attempts: int = 0
    error: str | None = None


def user_skipped(kind: str = VIDEO) -> M:
    return M(MediaStatus.SKIPPED, kind, wanted=False, reason=USER_SKIP_REASON)


def prefs_skipped(kind: str = IMAGE) -> M:
    return M(MediaStatus.SKIPPED, kind, wanted=False, reason=KIND_DISABLED_REASON)


def new_creator(s: Session, **kw: Any) -> Creator:
    creator = Creator(campaign_id=str(next(_campaign_ids)), name="Example Creator", **kw)
    s.add(creator)
    s.flush()
    return creator


def seed(factory, items: list[M], *, can_view: bool = True, **creator_kw: Any) -> int:
    """One post holding `items`, its status derived from them. Returns the post id."""
    with session_scope(factory) as s:
        creator = new_creator(s, **creator_kw)
        post = Post(
            post_id="p1",
            creator_id=creator.id,
            title="A post",
            current_user_can_view=can_view,
            urls_fetched_at=datetime.now(UTC),
        )
        s.add(post)
        s.flush()
        for n, m in enumerate(items):
            item = MediaItem(
                creator_id=creator.id,
                media_key=f"m{n}",
                kind=m.kind,
                status=m.status,
                status_reason=m.reason,
                wanted=m.wanted,
                attempts=m.attempts,
                last_error=m.error,
            )
            post.media_items.append(item)
            s.flush()
            if m.job:
                s.add(
                    DownloadJob(
                        media_item_id=item.id,
                        post_id=post.id,
                        creator_id=creator.id,
                        status=m.job,
                        attempt=m.attempts + 1,
                    )
                )
        post.status = derive_post_status(post)
        return post.id


def load(s: Session, post_id: int) -> Post:
    return s.execute(
        select(Post)
        .options(selectinload(Post.media_items), selectinload(Post.creator))
        .where(Post.id == post_id)
    ).scalar_one()


def jobs(s: Session, post_id: int) -> list[DownloadJob]:
    return list(
        s.execute(
            select(DownloadJob).where(DownloadJob.post_id == post_id).order_by(DownloadJob.id)
        ).scalars()
    )


def resource(res: dict[str, Any], included: list[dict[str, Any]] | None = None):
    return post_from_resource(res, IncludedIndex(fx.posts_page([res], included=included)))


# ---- actions ---------------------------------------------------------------------------


def first(fn: str) -> Callable[[MediaLifecycle, Session, Post], Any]:
    return lambda lc, s, post: getattr(lc, fn)(s, post.media_items[0])


def remove_first_job(lc: MediaLifecycle, s: Session, post: Post) -> None:
    lc.remove_job(s, jobs(s, post.id)[0])


def set_creator(**values: Any) -> Callable[[MediaLifecycle, Session, Post], None]:
    def act(lc: MediaLifecycle, s: Session, post: Post) -> None:
        for key, value in values.items():
            setattr(post.creator, key, value)
        lc.apply_creator_prefs(s, post.creator)

    return act


@dataclass
class Case:
    name: str
    before: list[M]
    act: Callable[[MediaLifecycle, Session, Post], Any]
    after: list[tuple[str, bool]]  # (status, wanted) of each item
    post: str
    jobs: list[str] = field(default_factory=list)  # every job's status, oldest first
    creator: dict[str, Any] = field(default_factory=dict)
    stopped: int = 0  # running downloads the workers are told to stop


S = MediaStatus
CASES = [
    Case(
        "skipping a queued item cancels its job",
        [M(S.QUEUED, job=JobStatus.QUEUED)],
        first("skip_media"),
        [(S.SKIPPED, False)],
        PostStatus.SKIPPED,
        [JobStatus.CANCELLED],
    ),
    Case(
        "skipping a downloading item stops the download",
        [M(S.DOWNLOADING, job=JobStatus.RUNNING)],
        first("skip_media"),
        [(S.SKIPPED, False)],
        PostStatus.SKIPPED,
        [JobStatus.RUNNING],  # its worker records the cancel
        stopped=1,
    ),
    Case(
        "skipping never touches an archived item",
        [M(S.COMPLETED)],
        first("skip_media"),
        [(S.COMPLETED, True)],
        PostStatus.COMPLETED,
    ),
    Case(
        "skipping a post keeps its archived video",
        [M(S.COMPLETED), M(S.QUEUED, IMAGE, job=JobStatus.QUEUED)],
        lambda lc, s, post: lc.skip_post(s, post),
        [(S.COMPLETED, True), (S.SKIPPED, False)],
        PostStatus.COMPLETED,
        [JobStatus.CANCELLED],
        creator={"include_images": True},
    ),
    Case(
        "unskipping a post respects the creator preferences",
        [user_skipped(VIDEO), user_skipped(IMAGE)],
        lambda lc, s, post: lc.unskip_post(s, post),
        [(S.DISCOVERED, True), (S.SKIPPED, False)],
        PostStatus.PENDING,
    ),
    Case(
        "unskipping a post brings back cancelled media",
        [M(S.CANCELLED)],
        lambda lc, s, post: lc.unskip_post(s, post),
        [(S.DISCOVERED, True)],
        PostStatus.PENDING,
    ),
    Case(
        "unskipping an item overrides the creator preferences",
        [prefs_skipped(IMAGE)],
        first("unskip_media"),
        [(S.DISCOVERED, True)],
        PostStatus.PENDING,
    ),
    Case(
        "retrying an item overrides the creator preferences",
        [prefs_skipped(IMAGE)],
        first("retry_media"),
        [(S.QUEUED, True)],
        PostStatus.PENDING,
        [JobStatus.QUEUED],
    ),
    Case(
        "retrying an archived item downloads it again",
        [M(S.COMPLETED)],
        first("retry_media"),
        [(S.QUEUED, True)],
        PostStatus.PENDING,
        [JobStatus.QUEUED],
    ),
    Case(
        "a forced post download re-queues archived media of wanted kinds only",
        [M(S.COMPLETED), prefs_skipped(IMAGE)],
        lambda lc, s, post: lc.download_post(s, post, force=True),
        [(S.QUEUED, True), (S.SKIPPED, False)],
        PostStatus.PENDING,
        [JobStatus.QUEUED],
    ),
    Case(
        "a plain post download leaves archived media alone",
        [M(S.COMPLETED), M(S.FAILED_PERMANENT)],
        lambda lc, s, post: lc.download_post(s, post),
        [(S.COMPLETED, True), (S.QUEUED, True)],
        PostStatus.PENDING,
        [JobStatus.QUEUED],
    ),
    Case(
        "a post download brings back what the user skipped",
        [user_skipped(VIDEO)],
        lambda lc, s, post: lc.download_post(s, post),
        [(S.QUEUED, True)],
        PostStatus.PENDING,
        [JobStatus.QUEUED],
    ),
    Case(
        "a post download re-queues cancelled media",
        [M(S.CANCELLED)],
        lambda lc, s, post: lc.download_post(s, post),
        [(S.QUEUED, True)],
        PostStatus.PENDING,
        [JobStatus.QUEUED],
    ),
    Case(
        "removing a queued job cancels its item",
        [M(S.QUEUED, job=JobStatus.QUEUED)],
        remove_first_job,
        [(S.CANCELLED, True)],
        PostStatus.SKIPPED,
        [JobStatus.CANCELLED],
    ),
    Case(
        "removing a running job stops the download",
        [M(S.DOWNLOADING, job=JobStatus.RUNNING)],
        remove_first_job,
        [(S.DOWNLOADING, True)],
        PostStatus.PENDING,
        [JobStatus.RUNNING],
        stopped=1,
    ),
    Case(
        "a cancelled image doesn't hold back a downloaded video",
        [M(S.COMPLETED), M(S.QUEUED, IMAGE, job=JobStatus.QUEUED)],
        remove_first_job,
        [(S.COMPLETED, True), (S.CANCELLED, True)],
        PostStatus.COMPLETED,
        [JobStatus.CANCELLED],
        creator={"include_images": True},
    ),
    Case(
        "retry all failed takes failed, failed permanently and missing, not cancelled",
        [M(S.FAILED_PERMANENT), M(S.MISSING), M(S.CANCELLED), M(S.FAILED)],
        lambda lc, s, post: lc.retry_failed(s),
        [(S.QUEUED, True), (S.QUEUED, True), (S.CANCELLED, True), (S.QUEUED, True)],
        PostStatus.PENDING,
        [JobStatus.QUEUED] * 3,
    ),
    Case(
        "clearing failed skips them and leaves cancelled alone",
        [M(S.FAILED_PERMANENT), M(S.MISSING), M(S.CANCELLED)],
        lambda lc, s, post: lc.clear_failed(s),
        [(S.SKIPPED, False), (S.SKIPPED, False), (S.CANCELLED, True)],
        PostStatus.SKIPPED,
    ),
    Case(
        "turning a kind off cancels its queued downloads; a running one finishes",
        [
            M(S.QUEUED, IMAGE, job=JobStatus.QUEUED),
            M(S.DOWNLOADING, IMAGE, job=JobStatus.RUNNING),
            M(S.COMPLETED),
        ],
        set_creator(include_images=False),
        [(S.SKIPPED, False), (S.DOWNLOADING, False), (S.COMPLETED, True)],
        PostStatus.COMPLETED,
        [JobStatus.CANCELLED, JobStatus.RUNNING],
        creator={"include_images": True},
    ),
    Case(
        "turning a kind on queues it when the creator auto-downloads",
        [prefs_skipped(IMAGE)],
        set_creator(include_images=True),
        [(S.QUEUED, True)],
        PostStatus.PENDING,
        [JobStatus.QUEUED],
    ),
    Case(
        "a user skip survives a preferences change",
        [user_skipped(IMAGE)],
        set_creator(include_images=True),
        [(S.SKIPPED, False)],
        PostStatus.SKIPPED,
    ),
    Case(
        "turning auto-download on queues waiting media",
        [M(S.DISCOVERED)],
        set_creator(auto_download=True),
        [(S.QUEUED, True)],
        PostStatus.PENDING,
        [JobStatus.QUEUED],
        creator={"auto_download": False},
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_action(case: Case, session_factory, lifecycle, workers):
    post_id = seed(session_factory, case.before, **case.creator)
    with session_scope(session_factory) as s:
        case.act(lifecycle, s, load(s, post_id))
    with session_scope(session_factory) as s:
        post = load(s, post_id)
        assert [(m.status, m.wanted) for m in post.media_items] == case.after
        assert post.status == case.post
        assert [j.status for j in jobs(s, post_id)] == case.jobs
    assert len(workers.cancelled) == case.stopped


def test_a_retry_starts_over_and_renews_the_links(session_factory, lifecycle, workers):
    post_id = seed(session_factory, [M(S.FAILED_PERMANENT, attempts=5)])
    with session_scope(session_factory) as s:
        job = lifecycle.retry_media(s, load(s, post_id).media_items[0])
        assert (job.attempt, job.priority) == (1, 10)
    with session_scope(session_factory) as s:
        post = load(s, post_id)
        assert post.media_items[0].attempts == 0
        assert post.urls_fetched_at is None  # the download fetches new links first
    assert workers.notified == 1


def test_a_first_download_keeps_fresh_links(session_factory, lifecycle):
    post_id = seed(session_factory, [M(S.DISCOVERED)], auto_download=False)
    with session_scope(session_factory) as s:
        lifecycle.download_post(s, load(s, post_id))
    with session_scope(session_factory) as s:
        assert load(s, post_id).urls_fetched_at is not None


# ---- events ----------------------------------------------------------------------------


def test_changes_are_announced_after_commit(session_factory, lifecycle, bus):
    post_id = seed(session_factory, [M(S.QUEUED, job=JobStatus.QUEUED)])
    seed(session_factory, [M(S.QUEUED, job=JobStatus.QUEUED)])  # another post, untouched
    with session_scope(session_factory) as s:
        lifecycle.skip_media(s, load(s, post_id).media_items[0])
        assert bus.events == []
    assert [(e, d.get("id"), d.get("status")) for e, d in bus.events] == [
        ("post.changed", post_id, PostStatus.SKIPPED),
        ("queue.changed", None, None),
    ]


def test_nothing_is_announced_when_the_session_rolls_back(session_factory, lifecycle, bus, workers):
    post_id = seed(session_factory, [M(S.DOWNLOADING, job=JobStatus.RUNNING)])
    with pytest.raises(RuntimeError), session_scope(session_factory) as s:
        lifecycle.skip_media(s, load(s, post_id).media_items[0])
        raise RuntimeError("the request failed after the action")
    assert bus.events == [] and workers.cancelled == []
    with session_scope(session_factory) as s:
        assert load(s, post_id).media_items[0].status == S.DOWNLOADING


def test_an_action_that_changes_nothing_announces_nothing(session_factory, lifecycle, bus):
    post_id = seed(session_factory, [M(S.COMPLETED)])
    with session_scope(session_factory) as s:
        lifecycle.skip_media(s, load(s, post_id).media_items[0])
    assert bus.events == []


# ---- post status -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("items", "can_view", "expected"),
    [
        ([], True, PostStatus.NO_MEDIA),
        ([M(S.COMPLETED)], True, PostStatus.COMPLETED),
        ([M(S.COMPLETED), M(S.CANCELLED, IMAGE)], True, PostStatus.COMPLETED),
        ([M(S.CANCELLED)], True, PostStatus.SKIPPED),
        ([prefs_skipped(IMAGE)], True, PostStatus.NO_MEDIA),
        ([user_skipped(VIDEO)], True, PostStatus.SKIPPED),
        ([M(S.COMPLETED), M(S.FAILED_PERMANENT)], True, PostStatus.PARTIAL),
        ([M(S.MISSING)], True, PostStatus.PARTIAL),
        ([M(S.UNSUPPORTED_DRM)], True, PostStatus.UNSUPPORTED),
        ([M(S.FAILED)], True, PostStatus.PENDING),  # waiting to retry
        ([M(S.COMPLETED)], False, PostStatus.NO_ACCESS),
    ],
    ids=[
        "no media",
        "archived",
        "video archived, image cancelled",
        "all cancelled",
        "kind excluded by the creator",
        "skipped by the user",
        "one archived, one failed",
        "file missing",
        "drm",
        "failed, retry pending",
        "locked",
    ],
)
def test_post_status_is_derived_from_its_media(session_factory, items, can_view, expected):
    post_id = seed(session_factory, items, can_view=can_view)
    with session_scope(session_factory) as s:
        assert load(s, post_id).status == expected


# ---- syncing ---------------------------------------------------------------------------


def test_a_rescan_marks_links_fresh_only_when_it_resyncs_the_media(session_factory, lifecycle):
    listing = resource(fx.native_video_post("p1"))
    edited = resource(fx.native_video_post("p1", edited_at="2026-04-01T00:00:00.000+00:00"))
    with session_scope(session_factory) as s:
        creator = new_creator(s)
        post = lifecycle.sync_post(s, creator, listing, resolve_media).post
        assert post.urls_fetched_at is not None
        post.urls_fetched_at = LONG_AGO
        # Unchanged: the media rows keep their old links, so the links stay stale and the
        # download renews them instead of trying an expired one.
        assert not lifecycle.sync_post(s, creator, listing, resolve_media).changed
        assert post.urls_fetched_at == LONG_AGO
        # Edited: the media are re-synced from the new data, so the links are fresh.
        lifecycle.sync_post(s, creator, edited, resolve_media)
        assert post.urls_fetched_at > LONG_AGO
        # The Refresh button and a download renewing its links always re-sync.
        post.urls_fetched_at = LONG_AGO
        lifecycle.sync_post(s, creator, edited, resolve_media, refresh=True)
        assert post.urls_fetched_at > LONG_AGO


def test_a_scan_reapplying_preferences_cancels_queued_downloads(session_factory, lifecycle):
    media = [fx.media_resource("m1")]
    with session_scope(session_factory) as s:
        creator = new_creator(s, include_images=True)
        post = lifecycle.sync_post(
            s, creator, resource(fx.image_post("p1", ["m1"]), media), resolve_media
        ).post
        assert jobs(s, post.id) and {j.status for j in jobs(s, post.id)} == {JobStatus.QUEUED}
        creator.include_images = False
        edited = fx.image_post("p1", ["m1"], edited_at="2026-04-01T00:00:00.000+00:00")
        lifecycle.sync_post(s, creator, resource(edited, media), resolve_media)
        assert {j.status for j in jobs(s, post.id)} == {JobStatus.CANCELLED}
        assert {(m.status, m.status_reason) for m in post.media_items} == {
            (S.SKIPPED, KIND_DISABLED_REASON)
        }


# ---- downloads -------------------------------------------------------------------------


def start(lifecycle: MediaLifecycle, factory, post_id: int) -> Claim:
    """Start the post's queued download, as claiming does."""
    with session_scope(factory) as s:
        claim = lifecycle.start_download(s, jobs(s, post_id)[-1], "w0")
    assert claim is not None
    return claim


def record(lifecycle: MediaLifecycle, factory, claim: Claim, outcome: Outcome) -> None:
    with session_scope(factory) as s:
        lifecycle.record_outcome(s, claim, outcome)


def queued(factory, **m: Any) -> int:
    return seed(factory, [M(S.QUEUED, job=JobStatus.QUEUED, **m)])


def test_starting_a_download_marks_it_running_and_announces_it_on_commit(
    session_factory, lifecycle, bus
):
    post_id = queued(session_factory)
    with session_scope(session_factory) as s:
        job = jobs(s, post_id)[0]
        claim = lifecycle.start_download(s, job, "w3")
        assert claim == Claim(job.id, job.media_item_id, post_id, 1)
        assert lifecycle.start_download(s, job, "w4") is None  # it's taken
        assert bus.events == []  # nothing before the commit
    with session_scope(session_factory) as s:
        post = load(s, post_id)
        job = jobs(s, post_id)[0]
        assert (job.status, job.worker_id, job.stage) == (JobStatus.RUNNING, "w3", "resolving")
        assert job.started_at is not None
        assert post.media_items[0].status == S.DOWNLOADING
    payload = {"id": claim.job_id, "media_item_id": claim.media_id, "post_id": post_id}
    assert bus.events == [("job.started", payload)]


def test_a_completed_download_archives_its_media(session_factory, lifecycle, bus):
    post_id = queued(session_factory)
    claim = start(lifecycle, session_factory, post_id)
    bus.events.clear()

    record(lifecycle, session_factory, claim, Completed("C/P/clip.mp4", 42, "abc", True))

    with session_scope(session_factory) as s:
        post = load(s, post_id)
        item, job = post.media_items[0], jobs(s, post_id)[0]
        assert (item.status, item.file_path, item.file_size_bytes, item.sha256) == (
            S.COMPLETED,
            "C/P/clip.mp4",
            42,
            "abc",
        )
        assert item.metadata_embedded and item.completed_at is not None
        assert (job.status, job.progress_percent, job.bytes_downloaded, job.stage) == (
            JobStatus.COMPLETED,
            100.0,
            42,
            None,
        )
        assert post.status == PostStatus.COMPLETED
        entry = s.execute(select(History)).scalar_one()
        assert entry.event_type == EventType.DOWNLOAD_COMPLETED
        assert entry.message == "Downloaded clip.mp4 (Example Creator / A post)"
    finished = {
        "id": claim.job_id,
        "media_item_id": claim.media_id,
        "post_id": post_id,
        "status": JobStatus.COMPLETED,
    }
    assert ("job.finished", finished) in bus.events
    assert ("post.changed", {"id": post_id, "creator_id": ANY, "status": "completed"}) in bus.events
    assert [e for e, _ in bus.events].count("history.added") == 1


@pytest.mark.parametrize("kind", list(FailureKind))
def test_each_kind_of_failure_leaves_its_media_where_the_table_says(
    session_factory, lifecycle, kind
):
    where = {
        FailureKind.DRM: (S.UNSUPPORTED_DRM, DRM_REASON),
        FailureKind.UNSUPPORTED: (S.UNSUPPORTED, "boom"),
        FailureKind.NO_ACCESS: (S.NO_ACCESS, "boom"),
        FailureKind.AUTH: (S.FAILED, "boom"),
        FailureKind.RETRYABLE: (S.FAILED, "boom"),
    }.get(kind, (S.FAILED_PERMANENT, "boom"))
    post_id = queued(session_factory)
    claim = start(lifecycle, session_factory, post_id)

    record(lifecycle, session_factory, claim, Failed(kind, "boom", "some_label"))

    with session_scope(session_factory) as s:
        item, job = load(s, post_id).media_items[0], jobs(s, post_id)[0]
        assert (item.status, item.status_reason, item.last_error) == (*where, "boom")
        assert (job.status, job.error, job.error_class) == (JobStatus.FAILED, "boom", "some_label")
        # Exactly the kinds a download keeps its partial files for are retried.
        assert (item.next_retry_at is not None) == kind.retried
        assert item.attempts == (0 if kind == FailureKind.AUTH else 1)


def test_retries_back_off_until_the_attempt_limit(session_factory, lifecycle, settings):
    settings.update({"downloads": {"max_attempts": 3, "retry_base_seconds": 60}})
    post_id = queued(session_factory, attempts=1)
    claim = start(lifecycle, session_factory, post_id)
    before = datetime.now(UTC)

    record(lifecycle, session_factory, claim, Failed(FailureKind.RETRYABLE, "HTTP 503", "server"))

    with session_scope(session_factory) as s:
        item = load(s, post_id).media_items[0]
        assert (item.status, item.attempts) == (S.FAILED, 2)
        # 60 s doubled for the second attempt, give or take 20%
        assert before + timedelta(seconds=95) < item.next_retry_at < before + timedelta(seconds=145)
        item.next_retry_at = datetime.now(UTC)  # due now
    assert lifecycle.retry_due() == 1
    with session_scope(session_factory) as s:
        retry = jobs(s, post_id)[-1]
        assert (retry.status, retry.attempt) == (JobStatus.QUEUED, 3)
        assert load(s, post_id).media_items[0].attempts == 2  # an automatic retry keeps them

    claim = start(lifecycle, session_factory, post_id)
    record(lifecycle, session_factory, claim, Failed(FailureKind.RETRYABLE, "HTTP 503", "server"))

    with session_scope(session_factory) as s:
        item = load(s, post_id).media_items[0]
        assert (item.status, item.status_reason, item.next_retry_at) == (
            S.FAILED_PERMANENT,
            "gave up after 3 attempts: HTTP 503",
            None,
        )


def test_a_signed_out_provider_retries_soon_without_using_an_attempt(
    session_factory, lifecycle, settings
):
    settings.update({"downloads": {"retry_base_seconds": 3600}})
    post_id = queued(session_factory)
    claim = start(lifecycle, session_factory, post_id)

    record(lifecycle, session_factory, claim, Failed(FailureKind.AUTH, "auth: expired", "auth"))

    with session_scope(session_factory) as s:
        item = load(s, post_id).media_items[0]
        assert item.attempts == 0
        assert item.next_retry_at <= datetime.now(UTC) + timedelta(minutes=10)


def test_a_cancelled_download_cancels_its_media(session_factory, lifecycle):
    post_id = queued(session_factory)
    claim = start(lifecycle, session_factory, post_id)

    record(lifecycle, session_factory, claim, Cancelled())

    with session_scope(session_factory) as s:
        post = load(s, post_id)
        item = post.media_items[0]
        assert (item.status, item.status_reason) == (S.CANCELLED, CANCELLED_REASON)
        assert jobs(s, post_id)[0].status == JobStatus.CANCELLED
        assert post.status == PostStatus.SKIPPED
        entry = s.execute(select(History)).scalar_one()
        assert entry.event_type == EventType.DOWNLOAD_CANCELLED


def test_media_skipped_while_downloading_stays_skipped_when_the_download_stops(
    session_factory, lifecycle, workers
):
    post_id = queued(session_factory)
    claim = start(lifecycle, session_factory, post_id)
    with session_scope(session_factory) as s:
        lifecycle.skip_media(s, load(s, post_id).media_items[0])
    assert workers.cancelled == [claim.job_id]

    record(lifecycle, session_factory, claim, Cancelled())

    with session_scope(session_factory) as s:
        item = load(s, post_id).media_items[0]
        assert (item.status, item.status_reason) == (S.SKIPPED, USER_SKIP_REASON)


def test_an_outcome_for_media_that_no_longer_exists_drops_the_download(
    session_factory, lifecycle, bus
):
    post_id = queued(session_factory)
    claim = start(lifecycle, session_factory, post_id)
    with session_scope(session_factory) as s:  # the creator was deleted, say
        s.delete(load(s, post_id).media_items[0])
    bus.events.clear()

    record(lifecycle, session_factory, claim, Completed("C/P/clip.mp4", 42, None, False))

    finished = {
        "id": claim.job_id,
        "media_item_id": claim.media_id,
        "post_id": post_id,
        "status": JobStatus.CANCELLED,
    }
    assert bus.events == [("job.finished", finished)]


def test_an_outcome_recorded_in_a_rolled_back_session_is_not_announced(
    session_factory, lifecycle, bus
):
    post_id = queued(session_factory)
    claim = start(lifecycle, session_factory, post_id)
    bus.events.clear()

    with pytest.raises(RuntimeError), session_scope(session_factory) as s:
        lifecycle.record_outcome(s, claim, Completed("C/P/clip.mp4", 42, None, False))
        raise RuntimeError("the commit never happens")

    assert bus.events == []
    with session_scope(session_factory) as s:
        assert load(s, post_id).media_items[0].status == S.DOWNLOADING


def test_a_restart_puts_interrupted_downloads_back_in_the_queue(session_factory, lifecycle):
    post_id = queued(session_factory)
    start(lifecycle, session_factory, post_id)

    assert lifecycle.recover_interrupted() == 1

    with session_scope(session_factory) as s:
        job = jobs(s, post_id)[0]
        assert (job.status, job.worker_id, job.stage) == (JobStatus.QUEUED, None, None)
        assert job.started_at is not None  # still counts toward the hourly limit
        assert load(s, post_id).media_items[0].status == S.QUEUED


# ---- upgrades --------------------------------------------------------------------------


def test_reconcile_brings_old_rows_under_the_current_rules_once(session_factory, lifecycle):
    skipped_with_video = seed(session_factory, [M(S.COMPLETED), user_skipped(IMAGE)])
    all_cancelled = seed(session_factory, [M(S.CANCELLED)])
    turned_off = seed(
        session_factory,
        [M(S.SKIPPED, IMAGE, wanted=False, reason=KIND_DISABLED_REASON, job=JobStatus.QUEUED)],
    )
    with session_scope(session_factory) as s:  # what the old rules stored
        load(s, skipped_with_video).status = PostStatus.SKIPPED
        load(s, all_cancelled).status = PostStatus.PENDING

    assert lifecycle.reconcile() == {"posts": 2, "jobs": 1, "drm": 0}
    with session_scope(session_factory) as s:
        assert load(s, skipped_with_video).status == PostStatus.COMPLETED
        assert load(s, all_cancelled).status == PostStatus.SKIPPED
        assert [j.status for j in jobs(s, turned_off)] == [JobStatus.CANCELLED]
        assert get_state(s, RULES_KEY) == RULES_VERSION
    assert lifecycle.reconcile() is None  # once per rules version


def test_reconcile_moves_yt_dlp_drm_failures_to_unsupported(session_factory, lifecycle):
    # yt-dlp's DRM errors used to fail media permanently instead of marking it unsupported.
    message = "ERROR: [vimeo] 12: This video is DRM protected"
    drm = seed(session_factory, [M(S.FAILED_PERMANENT, reason=message, error=message)])
    private = seed(session_factory, [M(S.FAILED_PERMANENT, error="ERROR: Private video")])
    in_a_word = seed(session_factory, [M(S.FAILED_PERMANENT, error="hydrma.example timed out")])

    assert lifecycle.reconcile()["drm"] == 1

    with session_scope(session_factory) as s:
        post = load(s, drm)
        assert (post.media_items[0].status, post.media_items[0].status_reason) == (
            S.UNSUPPORTED_DRM,
            DRM_REASON,
        )
        assert post.status == PostStatus.UNSUPPORTED
        assert load(s, private).media_items[0].status == S.FAILED_PERMANENT
        assert load(s, in_a_word).media_items[0].status == S.FAILED_PERMANENT


def test_a_failed_announcement_does_not_undo_the_change(session_factory, settings, workers):
    class BrokenBus:
        def publish(self, event, data=None):
            raise RuntimeError("subscriber went away")

    lifecycle = MediaLifecycle(session_factory, BrokenBus(), settings, workers)
    post_id = seed(session_factory, [M(S.QUEUED, job=JobStatus.QUEUED)])
    with session_scope(session_factory) as s:
        lifecycle.skip_media(s, load(s, post_id).media_items[0])
    with session_scope(session_factory) as s:
        assert load(s, post_id).media_items[0].status == S.SKIPPED

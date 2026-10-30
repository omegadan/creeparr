# Architecture

Single container, single process:

```
FastAPI (uvicorn)
├── /api/v1/*         REST + SSE (/api/v1/events)
├── /                 built React SPA (backend/creeparr/static)
├── ScanManager       one worker; walks posts via each provider, hands each to MediaLifecycle
├── DownloadManager   N asyncio workers: Claims picks the next download, DownloadRun runs it,
│                     MediaLifecycle records how it ended
├── MediaLifecycle    every media status change: user actions, post syncs, download
│                     outcomes, file checks; derives post status
├── Library           upkeep of the archived files: verify, restamp, embed backlog
├── APScheduler       scan_monitored / full_rescan / requeue_failed / session_check /
│                     prune / verify_files / embed_backlog / restamp_files
└── SQLite (WAL)      creators, posts, media_items, download_jobs, scan_runs, history, settings
```

## Data flow

1. **Add creator** → the provider resolves a URL/handle to a creator (Patreon:
   `resolve_campaign_id`, search API then page scrape) → row in `creators` → full scan requested.
2. **Scan** (`scanner/scanner.py`) iterates the provider's post pages. Each
   page is processed in one DB transaction, each post through `MediaLifecycle.sync_post`
   (`core/lifecycle.py`): upsert the post row; for a new or changed post, re-sync its media
   rows from `resolve_media` (post JSON → `MediaSpec` list) and mark its links fresh; apply
   the creator's preferences; queue wanted media if the creator auto-downloads; derive the
   post status. Incremental scans stop after `scan.overlap_posts` consecutive already-known,
   unchanged posts.
3. **Download** (`downloader/`): a worker (`manager.py`) asks `Claims` (`claims.py`) for the
   next download. The claim query applies every skip rule itself (disabled or throttled
   provider, pacing, creator at its cap), so the queue can be any length without jobs
   starving behind ones that can't start, and `MediaLifecycle.start_download` marks the
   winner running. `DownloadRun` (`run.py`) turns the claim into an outcome without
   touching any status:
   - If the post's signed URLs are stale (too old, or marked stale by a user retry) or the
     job is a retry attempt, it renews them first (`sync_post` with `refresh=True`).
   - It writes sidecars, checks for DRM (the provider's flag, or a probe of the HLS
     playlist), and hands off to a handler at the seam in `handlers/base.py`:
     `handlers/direct.py` (httpx streaming, resuming a `.part` file at the final name) or
     `handlers/ytdlp.py` (yt-dlp as a library for Mux HLS, YouTube, Vimeo and Reddit video).
     yt-dlp works in a scratch folder, because an undated post (YouTube's back catalogue)
     learns its date, and so its folder, from the download.
   - Files land at names rendered from the naming templates (`paths.py`), reserved against
     the other running downloads (`placement.py`). Videos get their post's metadata and
     cover embedded (`embed.py`, shared with the embed backlog), then dedupe, the NFO and
     the post's date on the file follow.

   The worker hands the outcome to `MediaLifecycle.record_outcome`, whose failure table
   decides the media's status and its retries (see the status model below).
4. **Events**: every state change publishes on an in-process `EventBus`; the SSE endpoint
   streams them and the UI invalidates TanStack Query caches accordingly. `MediaLifecycle`
   publishes `post.changed` for each post it changed, `queue.changed` when jobs changed,
   `job.started` and `job.finished` (with the post's id) and the history entries it wrote,
   only after the transaction commits (nothing if it rolls back), and wakes the workers
   then.

## Status model

`core/lifecycle.py` owns these rules; [GLOSSARY.md](../GLOSSARY.md) defines the terms.

`MediaItem.status`: `discovered → queued → downloading → completed`, with `failed` (retry
scheduled by `next_retry_at`), `failed_permanent`, `cancelled`, `skipped`, `unsupported`,
`unsupported_drm`, `no_access` and `missing` (archived, but the file is gone).

- **Failed** everywhere (post summaries, the failed list, status counts, retry all) means
  `failed`, `failed_permanent` or `missing` (`FAILED_MEDIA_STATUSES`).
- **Cancelled** media was stopped by the user (removed from the queue, or its download
  cancelled). It stays wanted and can be retried, but nothing re-queues it on its own, and it
  counts with skipped.
- `Post.status` is always derived from its media, never set: `skipped` when nothing is left to
  archive and the user skipped or cancelled some of it, `no_media` when the creator's
  preferences exclude all of it, otherwise from the wanted, not stopped media (`pending`,
  `completed`, `partial`, `unsupported`). A downloaded video with a cancelled image is
  `completed`.

`wanted` is decided by the creator's include flags (video always), except where the user
skipped an item. Post-level actions (download, skip, unskip a post) respect the flags;
item-level actions (retry, skip, unskip one item, and the failed list's retry all and clear)
override them. Flipping a flag re-applies it to existing media, queues newly wanted items, and
cancels the queued downloads of items no longer wanted (a running download finishes).

A download ends completed, failed or cancelled. A failure's kind alone decides what
happens to its media: DRM → `unsupported_drm`; unsupported → `unsupported`; no access →
`no_access`; a file or post that's gone, missing YouTube cookies or an unavailable video →
`failed_permanent`; anything else → `failed`, retried with exponential back-off until
`downloads.max_attempts`. A signed-out provider doesn't use up an attempt and is retried
within 10 minutes. A cancelled download cancels its media, unless the user skipped it while
it ran.

A user retry or re-download starts over: `attempts` goes back to 0 and the post's links are
marked stale so the download renews them first. Automatic retries keep counting. Skipping
never touches an archived item.

The library's `verify_files` task marks archived media whose file has vanished as
`missing` (queuing it again with `downloads.requeue_missing`) and restores it when the file
comes back, through the lifecycle like every other status change.

When these rules change, `RULES_VERSION` goes up and `MediaLifecycle.reconcile` re-derives
the stored rows once, at the next startup.

## Auth handling

API traffic goes through each provider's client; session cookies are only sent to that
provider's own hosts (never CDNs). Any `AuthError`/`CloudflareChallengeError` marks that
provider's auth status invalid, which pauses its scans, holds its native downloads (embeds
continue), shows a banner, and records history. A daily `session_check` and a successful
**Test connection** clear it.

## Providers

Anything provider-specific lives behind `creeparr/providers/base.py:ProviderService`: fetch the
logged-in user, resolve a creator from a URL/handle, list subscriptions, iterate posts, turn a
post into `MediaSpec`s, and stream media. `PatreonProvider` wraps the original Patreon client;
`OnlyFansProvider` implements OnlyFans' signed private API (rules fetched at runtime);
`YouTubeProvider` lists channels with yt-dlp; `InstagramProvider` uses gallery-dl;
`RedditProvider` reads the public JSON listings. The
`ProviderRegistry` maps `creators.provider` to the right service; the scanner, downloader, API
and scheduler are provider-neutral and go through it. Auth status is tracked per provider.

Adding a provider: implement `ProviderService`, register it in `services.py`, add a settings
group, and it appears in the UI automatically (Add-creator picker, its Settings tab, badges).

## Not yet implemented / ideas

- NFO/poster sidecars for Jellyfin/Plex (`naming.write_nfo` reserved)
- Notifications (Discord/webhook), API-key auth for the UI, "rename files" task
- Shop/product purchases, comments archiving

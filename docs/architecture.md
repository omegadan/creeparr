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
   (`core/lifecycle.py`): upsert the post row; for a new or changed post, or one with media
   queued or failed, re-sync its media rows from `resolve_media` (post JSON → `MediaSpec`
   list) and mark its links fresh (so a scan renews links that can't be renewed one post at a
   time, without counting the post as changed); apply
   the creator's preferences; queue wanted media if the creator auto-downloads; derive the
   post status. Incremental scans stop after `scan.overlap_posts` consecutive already-known,
   unchanged posts.
3. **Download** (`downloader/`): a worker (`manager.py`) asks `Claims` (`claims.py`) for the
   next download. The claim query applies every skip rule itself (disabled or throttled
   provider, pacing, creator at its cap), so the queue can be any length without jobs
   starving behind ones that can't start, and `MediaLifecycle.start_download` marks the
   winner running. `DownloadRun` (`run.py`) turns the claim into an outcome without
   touching any status:
   - **Link renewal**: if the provider says renewal applies to the item (`renews_links`: not
     an embed, an OnlyFans message, an Instagram story or highlight, or anything on Reddit)
     and its links may have expired (too old, marked stale by a user retry, or a retry
     attempt), it fetches the post again (`get_post`, given the stored post type) and syncs
     it (`sync_post` with `refresh=True`). Otherwise it downloads with the stored link.
   - It writes sidecars, asks the provider whether the item is DRM-protected
     (`drm_protected`: the flag stored when read, or Patreon's check of its Mux playlists),
     and builds a `FetchPlan`: the media session of whoever hosts the file
     (`ProviderRegistry.hosting`: YouTube for YouTube videos, whoever posted them; else the
     posting provider), the container (the setting, or the creator's provider's
     `video_container` for "auto") and the sign-in message. It hands both to a handler at
     the seam in `handlers/base.py`:
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
   then. Each event is a model in `core/server_events.py` (see the wire contract below).

## Wire contract

Every JSON response and every server-sent event is declared once, as a pydantic model:
responses in `api/schemas.py` (each route names its `response_model`), events in
`core/server_events.py` (`EventBus.publish` takes one). `api/contract.py` adds the events to
the OpenAPI description and generates `frontend/src/api/schema.ts` from it; `types.ts` only
names those types for the UI, and `events.ts` types each handler by its event.

- After changing a response or an event, run `make api-types`. A backend test fails while
  `schema.ts` is stale, and another fails on a JSON route with no declared response.
- Statuses go over the wire as their enums (`db/enums.py`, `ClaimState`), so the UI's unions
  are generated. The UI still renders a value it doesn't know: a tab opened before an
  upgrade can meet one.
- Which actions a post or media item offers comes from the lifecycle (`item_actions`,
  `post_actions`), sent as `actions`; the UI shows a button per action.
- The `hello` event carries the server's version. A tab that reconnects to a different
  version (the container restarted on a new image) offers to reload.

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
post into `MediaSpec`s, fetch a post again, and fetch its media. `PatreonProvider` wraps the
original Patreon client;
`OnlyFansProvider` implements OnlyFans' signed private API (rules fetched at runtime);
`YouTubeProvider` lists channels with yt-dlp; `InstagramProvider` uses gallery-dl;
`RedditProvider` reads the public JSON listings. The
`ProviderRegistry` maps `creators.provider` to the right service; the scanner, downloader, API
and scheduler are provider-neutral and go through it. Auth status is tracked per provider.

Fetching media (`providers/media.py`): each provider builds a `MediaSession` from its current
settings: headers, the session cookie and the one domain it may go to, the cookies.txt for
yt-dlp, the curl_cffi impersonation target and, for Patreon, the API's rate limiter.
`MediaSession.stream` is the only media stream, used for files and covers: it scopes the
cookie, sends the Range header, retries a dropped connection once and opens a connection per
call, closed with the response, so a settings change never closes one in use. Each provider
also answers `renews_links(media)` and `drm_protected(media)`, and declares the embeds it
`hosts` (YouTube: YouTube videos) and its `video_container` (YouTube: MKV).

Each provider class carries a `ProviderDescription` (`providers/description.py`): its name
(also its settings group and `creators.provider`), label, account fields with their labels and
hints, setup text, the post types its scans store with their labels, and `video_container`,
`hosts` and `sign_in_help`. `/providers` serves it, and the UI takes provider labels, badges,
Settings tabs, the account form, post-type labels and download-folder rows from there. Secrets
are marked on the settings model's fields (`SecretStr`, `SecretText`), and a provider's own
archive root is read from `CREEPARR_<NAME>_DOWNLOAD_DIR`. A provider can add its own check
that a post changed (`post_changed`, from the stored JSON; Patreon compares its file link).

Adding a provider:
1. Its package: a `ProviderService` subclass with its `description`.
2. Its settings model, as a field of `AppSettings` named after it, with secrets marked.
3. One line in `providers/catalog.py`, then `make api-types` (the settings and paths
   responses list each provider).

It then shows up everywhere in the UI. Its Settings tab is the generic one (account and
downloads per hour) until it gets a hand-made form in `SettingsPage`'s `PROVIDER_FORMS`. The
deploy files (`docker-compose.yml`, `env.example`, `unraid/creeparr.xml`) list its download
folder variable for Docker and Unraid users.

## Not yet implemented / ideas

- NFO/poster sidecars for Jellyfin/Plex (`naming.write_nfo` reserved)
- Notifications (Discord/webhook), API-key auth for the UI, "rename files" task
- Shop/product purchases, comments archiving

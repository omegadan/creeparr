# Architecture

Single container, single process:

```
FastAPI (uvicorn)
├── /api/v1/*         REST + SSE (/api/v1/events)
├── /                 built React SPA (backend/creeparr/static)
├── ScanManager       one worker; walks posts via PatreonClient, upserts rows, queues media
├── DownloadManager   N asyncio workers; claims jobs, runs handlers, retries with back-off
├── APScheduler       scan_monitored / full_rescan / requeue_failed / session_check / prune
└── SQLite (WAL)      creators, posts, media_items, download_jobs, scan_runs, history, settings
```

## Data flow

1. **Add creator** → `PatreonClient.resolve_campaign_id` (search API, then page scrape) →
   `get_campaign` → row in `creators` → full scan requested.
2. **Scan** (`scanner/scanner.py`) iterates `GET /api/posts?filter[campaign_id]=…` pages. Each
   page is processed in one DB transaction: `upsert_post` → `resolve_media` (post JSON →
   `MediaSpec` list) → `sync_media_items` → `auto_queue_post` → `recompute_post_status`.
   Incremental scans stop after `scan.overlap_posts` consecutive already-known, unchanged posts.
3. **Download** (`downloader/manager.py`): a worker atomically claims a `queued` job, refreshes
   the post's signed URLs if stale, writes sidecars, probes HLS playlists for DRM, then hands
   off to `handlers/direct.py` (httpx streaming with `.part` resume) or `handlers/ytdlp.py`
   (yt-dlp as a library for Mux HLS and YouTube/Vimeo). Results are moved atomically into the
   final path rendered from the naming templates.
4. **Events**: every state change publishes on an in-process `EventBus`; the SSE endpoint
   streams them and the UI invalidates TanStack Query caches accordingly.

## Status model

`MediaItem.status`: `discovered → queued → downloading → completed`, with `failed` (retry
scheduled by `next_retry_at`), `failed_permanent`, `cancelled`, `skipped`, `unsupported`,
`unsupported_drm`, `no_access`. `Post.status` is derived from its wanted media
(`recompute_post_status`).

`wanted` is decided by the creator's include flags (video always). Flipping a flag re-applies
prefs to existing media and queues newly wanted items.

## Auth handling

All API traffic goes through `PatreonClient`; yt-dlp only ever gets media/embed URLs. Any
`AuthError`/`CloudflareChallengeError` marks `auth_status` invalid, which pauses scans, holds
Patreon-hosted downloads (embeds continue), shows a banner, and records history. A daily
`session_check` and a successful **Test connection** clear it.

## Providers

Anything provider-specific lives behind `creeparr/providers/base.py:ProviderService`: fetch the
logged-in user, resolve a creator from a URL/handle, list subscriptions, iterate posts, turn a
post into `MediaSpec`s, and stream media. `PatreonProvider` wraps the original Patreon client;
`OnlyFansProvider` implements OnlyFans' signed private API (rules fetched at runtime). The
`ProviderRegistry` maps `creators.provider` to the right service; the scanner, downloader, API
and scheduler are provider-neutral and go through it. Auth status is tracked per provider.

Adding a provider: implement `ProviderService`, register it in `services.py`, add a settings
group, and it appears in the UI automatically (Add-creator picker, Accounts tab, badges).

## Not yet implemented / ideas

- NFO/poster sidecars for Jellyfin/Plex (`naming.write_nfo` reserved)
- Notifications (Discord/webhook), API-key auth for the UI, "rename files" task
- Shop/product purchases, comments archiving

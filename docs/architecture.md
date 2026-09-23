# Architecture

Single container, single process:

```
FastAPI (uvicorn)
├── /api/v1/*         REST + SSE (/api/v1/events)
├── /                 built React SPA (backend/creeparr/static)
├── ScanManager       one worker; walks posts via each provider, upserts rows, queues media
├── DownloadManager   N asyncio workers; claims jobs, runs handlers, retries with back-off
├── APScheduler       scan_monitored / full_rescan / requeue_failed / session_check /
│                     prune / verify_files / embed_backlog
└── SQLite (WAL)      creators, posts, media_items, download_jobs, scan_runs, history, settings
```

## Data flow

1. **Add creator** → the provider resolves a URL/handle to a creator (Patreon:
   `resolve_campaign_id`, search API then page scrape) → row in `creators` → full scan requested.
2. **Scan** (`scanner/scanner.py`) iterates the provider's post pages. Each
   page is processed in one DB transaction: `upsert_post` → `resolve_media` (post JSON →
   `MediaSpec` list) → `sync_media_items` → `auto_queue_post` → `recompute_post_status`.
   Incremental scans stop after `scan.overlap_posts` consecutive already-known, unchanged posts.
3. **Download** (`downloader/manager.py`): a worker atomically claims a `queued` job, refreshes
   the post's signed URLs if stale, writes sidecars, probes HLS playlists for DRM, then hands
   off to `handlers/direct.py` (httpx streaming with `.part` resume) or `handlers/ytdlp.py`
   (yt-dlp as a library for Mux HLS, YouTube, Vimeo and Reddit video). Results are moved
   atomically into the final path rendered from the naming templates. The claim query
   applies every skip rule (disabled or throttled provider, creator at its cap) itself, so
   the queue can be any length without jobs starving behind ones that can't start.
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

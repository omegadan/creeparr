# Configuration reference

## Environment variables

See the table in the README. They cover paths, port and log level only. `PATREARR_ONLYFANS_DOWNLOAD_DIR` sends OnlyFans content to a separate directory; if unset it falls back to `PATREARR_DOWNLOAD_DIR`.

## Settings (stored in the database, edited in the UI or via `PUT /api/v1/settings`)

| Group.key | Default | Notes |
| --- | --- | --- |
| patreon.session_id | "" | Secret; set via `PUT /settings/patreon-auth` |
| patreon.cookies_txt | "" | Secret; optional Netscape cookie export |
| patreon.user_agent | Patreon mobile UA | |
| patreon.requests_per_second | 1.0 | Steady API rate limit (0.1–10) |
| patreon.random_delay_min / random_delay_max | 0 / 0 | Extra random pause (s) added before each request; 0/0 disables |
| onlyfans.requests_per_second | 0.5 | Steady API rate limit (0.1–5) |
| onlyfans.random_delay_min / random_delay_max | 0 / 0 | Extra random pause (s) before each OnlyFans request |
| patreon.http_backend | httpx | `curl_cffi` for Chrome TLS impersonation |
| scan.interval_minutes | 60 | 0 disables scheduled scans |
| scan.overlap_posts | 10 | Incremental stop condition |
| scan.full_rescan_days | 0 | 0 disables periodic full rescans |
| scan.default_* | | Defaults for new creators |
| downloads.concurrency | 2 | 1–8 workers |
| downloads.max_per_creator | 2 | |
| downloads.min_free_mb | 1024 | Queue pauses below this |
| downloads.max_attempts | 5 | |
| downloads.retry_base_seconds / retry_cap_seconds | 60 / 21600 | Exponential back-off with jitter |
| downloads.url_max_age_minutes | 30 | Re-fetch post before download if URLs are older |
| downloads.hls_fragment_concurrency | 4 | yt-dlp `concurrent_fragment_downloads` |
| downloads.compute_sha256 | true | |
| downloads.video_format | `bv*+ba/b` | yt-dlp format selector |
| downloads.ytdlp_remote_components | true | Let yt-dlp fetch its YouTube challenge solver (ejs) from GitHub |
| naming.post_folder_template | `{creator}/{published:%Y-%m-%d} - {title} [{post_id}]` | |
| naming.file_template | `{filename}` | |
| naming.max_component_length | 150 | bytes |
| naming.write_nfo | false | Kodi/Jellyfin/Plex .nfo per video |
| naming.embed_metadata | false | Embed title/description/date + cover thumbnail into videos via ffmpeg |
| naming.write_sidecars | true | |
| history.retention_days | 90 | |
| history.job_retention_days | 30 | |

Template tokens: `creator, creator_vanity, campaign_id, title, post_id, published (datetime,
supports strftime specs), post_type, filename, ext, media_kind, media_index, embed_provider`.

## REST API

Interactive docs at `/api/docs`. Main endpoints:

- `GET /api/v1/system/status`, `/system/logs`, `/system/tasks`, `POST /system/tasks/{name}/run`
- `GET /api/v1/events` (SSE)
- `GET/POST /api/v1/creators`, `POST /creators/lookup`, `GET /patreon/pledges`,
  `POST /creators/import-pledges`, `GET/PATCH/DELETE /creators/{id}`, `POST /creators/{id}/scan`,
  `POST /creators/scan-all`, `GET /creators/{id}/scans`
- `GET /api/v1/posts`, `GET /posts/{id}`, `GET /posts/{id}/raw`,
  `POST /posts/{id}/download|skip|unskip|refresh`, `POST /media/{id}/retry|skip|unskip`
- `GET /api/v1/queue`, `DELETE /queue/{job}`, `POST /queue/{job}/retry`,
  `POST /queue/pause|resume|retry-failed`, `DELETE /queue/failed`
- `GET /api/v1/history`
- `GET/PUT /api/v1/settings`, `PUT/DELETE /settings/patreon-auth`,
  `POST /settings/patreon-auth/test`, `POST /settings/naming-preview`

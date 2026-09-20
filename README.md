# Patreonarr

A self-hosted, *arr-style archiver for the Patreon creators you support. Add a creator, and
Patreonarr backs up every post you have access to, then keeps checking for new ones.

- Archives **native Patreon video** (direct files and Mux streams), **YouTube / Vimeo embeds**,
  and optionally images, audio and attachments per creator.
- **Import your pledges** in one click, or add creators by URL / vanity name / campaign id.
- Sonarr-like dark web UI: creators grid, per-creator post list with per-file status, live
  download queue, history, settings, system status and logs.
- Scheduled incremental scans, full back-fills, retries with back-off, DRM detection,
  disk-space guard, resumable downloads, `post.json` / `post.md` / `post.html` sidecars.
- One container, SQLite, no external services. `PUID`/`PGID`/`TZ` like linuxserver images.

> Patreonarr only reads content **your own account** can already see. It never bypasses
> access controls, skips DRM-protected media, and is intended for personal archival of the
> content you pay for. Do not use it to redistribute creators' work.

## Quick start

```yaml
# docker-compose.yml
services:
  patreonarr:
    build: .          # or image: ghcr.io/<you>/patreonarr:latest once published
    container_name: patreonarr
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Europe/London
    volumes:
      - ./config:/config
      - /mnt/media/patreon:/downloads
    ports:
      - "7979:7979"
    restart: unless-stopped
```

```bash
docker compose up -d
open http://localhost:7979
```

Then, in **Settings → Patreon**:

1. Log in to patreon.com in your browser.
2. Open DevTools → Application → Cookies → `https://www.patreon.com` and copy the value of
   the `session_id` cookie.
3. Paste it into Patreonarr and click **Save & connect**. You should see your account name.
4. Go to **Creators → Import pledges** (or **Add creator**) and pick who to archive.

A full back-fill starts immediately; after that, monitored creators are re-scanned every
60 minutes (configurable). New media is queued and downloaded automatically.

### Files on disk

```
/downloads/
└── Creator Name/
    └── 2026-03-14 - Episode 12 [98765432]/
        ├── episode-12.mp4
        ├── post.json      # raw API record
        ├── post.md        # post text as Markdown with front matter
        └── post.html      # post text as HTML
```

The folder and file templates are editable in **Settings → Naming & general**.

## Configuration

Environment variables (container-level):

| Variable                  | Default      | Purpose                              |
| ------------------------- | ------------ | ------------------------------------ |
| `PUID` / `PGID`           | `1000`       | User/group that owns files           |
| `TZ`                      | `Etc/UTC`    | Time zone                            |
| `PATREONARR_CONFIG_DIR`   | `/config`    | Database, logs, cookie file          |
| `PATREONARR_DOWNLOAD_DIR` | `/downloads` | Archive root                         |
| `PATREONARR_PORT`         | `7979`       | HTTP port                            |
| `PATREONARR_LOG_LEVEL`    | `INFO`       | `DEBUG`, `INFO`, `WARNING`, `ERROR`  |

Everything else (scan interval, concurrency, retries, naming, per-kind toggles, HTTP backend)
lives in the database and is edited in the UI. See [docs/configuration.md](docs/configuration.md).

## If Patreon blocks requests

Patreon sits behind Cloudflare. If **Test connection** reports a *Cloudflare challenge*:

1. Paste a full `cookies.txt` export (e.g. the "Get cookies.txt LOCALLY" extension) instead
   of only `session_id`; the `cf_clearance` cookie is forwarded.
2. Switch **HTTP backend** to `curl_cffi` (Chrome TLS impersonation). It is included in the
   Docker image.

## Development

```bash
# backend (Python 3.12, uv)
cd backend && uv sync --all-extras
PATREONARR_CONFIG_DIR=../config PATREONARR_DOWNLOAD_DIR=../downloads \
  uv run uvicorn patreonarr.app:create_app --factory --reload --port 7979
uv run pytest -q && uv run ruff check .

# frontend (Node 22)
cd frontend && npm ci && npm run dev     # proxies /api to :7979
npm run build                            # output in frontend/dist

make build   # builds the UI and copies it into backend/patreonarr/static
make docker  # builds the image
```

Layout: `backend/patreonarr/{patreon,scanner,downloader,api,core,db}` and
`frontend/src/{pages,components,api}`. Architecture notes in [docs/architecture.md](docs/architecture.md);
what was verified about Patreon's private API in [docs/patreon-api.md](docs/patreon-api.md);
manual test plan in [docs/e2e-checklist.md](docs/e2e-checklist.md).

## Status

Early release. Verified against synthetic API fixtures and unit/integration tests; the
end-to-end checklist against a real Patreon account still needs to be run (see
`docs/e2e-checklist.md`). Field names in Patreon's private API can change without notice.

## License

MIT

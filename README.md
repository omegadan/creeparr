# Creeparr

A self-hosted, *arr-style archiver for the Patreon creators you support. Add a creator, and
Creeparr backs up every post you have access to, then keeps checking for new ones.

- Archives **native Patreon video** (direct files and Mux streams), **YouTube / Vimeo embeds**,
  and optionally images, audio and attachments per creator.
- **Import your pledges** in one click, or add creators by URL / vanity name / campaign id.
- Light/dark/auto web UI (theme switch in the sidebar; auto follows your OS): creators grid, per-creator post list with per-file status, live
  download queue, history, settings, system status and logs.
- Scheduled incremental scans, full back-fills, retries with back-off, DRM detection,
  disk-space guard, resumable downloads, `post.json` / `post.md` / `post.html` sidecars.
- One container, SQLite, no external services. `PUID`/`PGID`/`TZ` like linuxserver images.

> Creeparr only reads content **your own account** can already see. It never bypasses
> access controls, skips DRM-protected media, and is intended for personal archival of the
> content you pay for. Do not use it to redistribute creators' work.

## Quick start

```yaml
# docker-compose.yml
services:
  creeparr:
    image: ghcr.io/omegadan/creeparr:latest   # or build: . to build from source
    container_name: creeparr
    environment:
      - PUID=99                 # Unraid defaults; use your own uid/gid elsewhere
      - PGID=100
      - TZ=America/Los_Angeles
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

Images are published to `ghcr.io/omegadan/creeparr` by GitHub Actions on every push to
`main` (`latest`) and on `v*` tags, for `linux/amd64` and `linux/arm64`. While the repository
is private the package is private too, so `docker login ghcr.io` with a token that has
`read:packages` before pulling.

Then, in **Settings → Accounts**, connect a provider:

**Patreon:**

1. Log in to patreon.com in your browser.
2. Open DevTools → Application → Cookies → `https://www.patreon.com` and copy the value of
   the `session_id` cookie.
3. Paste it into Creeparr and click **Save & connect**. You should see your account name.
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

## Unraid

An Unraid template is provided. Docker tab → Add Container → paste this Template URL:

```
https://raw.githubusercontent.com/omegadan/creeparr/main/unraid/creeparr.xml
```

See [unraid/README.md](unraid/README.md) for details.

## Configuration

Copy `.env.example` to `.env` next to `docker-compose.yml` and edit it; Compose picks it up
automatically. `CONFIG_DIR` and `DOWNLOAD_DIR` are required; the rest have defaults.
Every variable is documented in that file. Summary:

| Variable               | Default       | Purpose                                                |
| ---------------------- | ------------- | ------------------------------------------------------ |
| `PUID` / `PGID`        | `99` / `100`  | User/group that owns files (Unraid defaults)           |
| `TZ`                   | `America/Los_Angeles` | Time zone                                      |
| `CONFIG_DIR`           | required      | Host path mounted at `/config` (database, logs)        |
| `DOWNLOAD_DIR`         | required      | Host path mounted at `/downloads` (Patreon archive root) |
| `ONLYFANS_DOWNLOAD_DIR`| required      | Host path mounted at `/downloads-onlyfans` (OnlyFans root) |
| `PORT`                 | `7979`        | Host port for the web UI                               |
| `CREEPARR_LOG_LEVEL`   | `INFO`        | `DEBUG`, `INFO`, `WARNING`, `ERROR`                    |
| `CREEPARR_CONFIG_DIR`  | `/config`     | In-container config path (only when not using Docker) |
| `CREEPARR_DOWNLOAD_DIR`| `/downloads`  | In-container archive path (only when not using Docker) |
| `CREEPARR_ONLYFANS_DOWNLOAD_DIR`| `/downloads-onlyfans` | OnlyFans archive path; falls back to the main one if unset |
| `CREEPARR_PORT`        | `7979`        | Port the server listens on                             |

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
CREEPARR_CONFIG_DIR=../config CREEPARR_DOWNLOAD_DIR=../downloads \
  uv run uvicorn creeparr.app:create_app --factory --reload --port 7979
uv run pytest -q && uv run ruff check .

# frontend (Node 22)
cd frontend && npm ci && npm run dev     # proxies /api to :7979
npm run build                            # output in frontend/dist

make build   # builds the UI and copies it into backend/creeparr/static
make docker  # builds the image
```

The Docker image is ~275 MB (static ffmpeg). Layout: `backend/creeparr/{patreon,scanner,downloader,api,core,db}` and
`frontend/src/{pages,components,api}`. Architecture notes in [docs/architecture.md](docs/architecture.md);
what was verified about Patreon's private API in [docs/patreon-api.md](docs/patreon-api.md);
manual test plan in [docs/e2e-checklist.md](docs/e2e-checklist.md).

## Status

Early release. Verified against synthetic API fixtures and unit/integration tests; the
end-to-end checklist against a real Patreon account still needs to be run (see
`docs/e2e-checklist.md`). Field names in Patreon's private API can change without notice.

## Security

The web UI is unauthenticated by default. If Creeparr is reachable beyond your own machine,
set a password in **Settings → Security**; it gates the whole API. Credentials you paste
(Patreon/OnlyFans cookies) are stored in the SQLite DB under `/config` and never shown
back in full.

## License

MIT

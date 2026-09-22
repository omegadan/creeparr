# ---- frontend build -----------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- backend ------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS backend

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    CREEPARR_CONFIG_DIR=/config \
    CREEPARR_DOWNLOAD_DIR=/downloads \
    CREEPARR_PORT=7979 \
    PUID=1000 \
    PGID=1000 \
    TZ=Etc/UTC

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu tzdata curl xz-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Static ffmpeg/ffprobe (glibc build) instead of the large apt ffmpeg + its deps.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz" ;; \
      arm64) url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz" ;; \
      *) echo "unsupported arch $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "$url" -o /tmp/ffmpeg.tar.xz; \
    mkdir -p /tmp/ff && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ff --strip-components=1; \
    mv /tmp/ff/ffmpeg /tmp/ff/ffprobe /usr/local/bin/; \
    rm -rf /tmp/ff /tmp/ffmpeg.tar.xz

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# yt-dlp needs a JavaScript runtime for YouTube; deno is its default.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra impersonate

COPY backend/ ./
RUN uv sync --frozen --no-dev --extra impersonate
COPY --from=frontend /src/dist/ ./creeparr/static/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Per-provider roots (CREEPARR_<PROVIDER>_DOWNLOAD_DIR) are deliberately not set
# here and not declared as volumes: unset, every provider archives under
# /downloads. docker-compose.yml and the Unraid template set them when the
# matching host path is configured. See env.example.
VOLUME ["/config", "/downloads"]
EXPOSE 7979
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:7979/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "creeparr"]

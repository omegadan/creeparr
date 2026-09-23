# syntax=docker/dockerfile:1
# Pinned tool images; bump deliberately.
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.18
ARG DENO_IMAGE=denoland/deno:bin-2.9.7

FROM ${UV_IMAGE} AS uv
FROM ${DENO_IMAGE} AS deno

# ---- frontend build -----------------------------------------------------------
# The built UI is plain JS/CSS, so build it once on the runner's own platform
# instead of under QEMU for every target architecture.
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- ffmpeg -------------------------------------------------------------------
# Static ffmpeg/ffprobe (glibc build) instead of the large apt ffmpeg + its deps,
# fetched in its own stage so curl/xz leftovers don't reach the final image.
FROM debian:bookworm-slim AS ffmpeg
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl xz-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64|arm64) url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${arch}-static.tar.xz" ;; \
      *) echo "unsupported arch $arch" >&2; exit 1 ;; \
    esac; \
    cd /tmp; \
    curl -fsSL "$url" -o ffmpeg-release-${arch}-static.tar.xz; \
    curl -fsSL "$url.md5" -o ffmpeg.md5; \
    md5sum -c ffmpeg.md5; \
    mkdir ff && tar -xJf ffmpeg-release-${arch}-static.tar.xz -C ff --strip-components=1; \
    mv ff/ffmpeg ff/ffprobe /usr/local/bin/

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
    && apt-get install -y --no-install-recommends gosu tzdata curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ffmpeg /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/
# yt-dlp needs a JavaScript runtime for YouTube; deno is its default.
COPY --from=deno /deno /usr/local/bin/deno

# uv is only mounted for these two steps, and its download cache lives in a build
# cache mount, so neither ends up in the image.
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra impersonate

COPY backend/ ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra impersonate
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
    CMD curl -fs "http://localhost:${CREEPARR_PORT:-7979}/health" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "creeparr"]

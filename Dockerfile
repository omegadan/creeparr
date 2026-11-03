# syntax=docker/dockerfile:1
# Every image below is pinned by digest, so a build uses exactly what was reviewed.
# Dependabot (.github/dependabot.yml) opens a PR when a tag's digest or version moves.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv
FROM denoland/deno:bin-2.9.8@sha256:b507583d3c68ba69f3ed2ce5969ae5b8b70f43421c781279c7aea06435263543 AS deno
# Static ffmpeg/ffprobe (statically linked, so they run on any base) instead of the
# large apt ffmpeg and its dependencies.
FROM mwader/static-ffmpeg:9.0.2-1@sha256:f5d100184884eb3e82b0eb14bb5ebf98da99826ac824989720297fb9ef21c822 AS ffmpeg

# ---- frontend build -----------------------------------------------------------
# The built UI is plain JS/CSS, so build it once on the runner's own platform
# instead of under QEMU for every target architecture.
FROM --platform=$BUILDPLATFORM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- backend ------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337 AS backend

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

COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/
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

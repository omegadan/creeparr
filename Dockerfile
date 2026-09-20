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
    PATREARR_CONFIG_DIR=/config \
    PATREARR_DOWNLOAD_DIR=/downloads \
    PATREARR_ONLYFANS_DOWNLOAD_DIR=/downloads-onlyfans \
    PATREARR_PORT=7979 \
    PUID=1000 \
    PGID=1000 \
    TZ=Etc/UTC

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg gosu tzdata curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# yt-dlp needs a JavaScript runtime for YouTube; deno is its default.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra impersonate

COPY backend/ ./
RUN uv sync --frozen --no-dev --extra impersonate
COPY --from=frontend /src/dist/ ./patrearr/static/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME ["/config", "/downloads", "/downloads-onlyfans"]
EXPOSE 7979
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:7979/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "patrearr"]

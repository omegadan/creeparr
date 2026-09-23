"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from creeparr import __version__
from creeparr.api.router import api_router
from creeparr.api.system import health_payload
from creeparr.config import EnvConfig, get_env_config
from creeparr.core.errors import AppError
from creeparr.db.migrate import run_migrations
from creeparr.logging_setup import setup_logging
from creeparr.providers.errors import ProviderError
from creeparr.services import Services, build_services

log = logging.getLogger(__name__)


def create_app(env: EnvConfig | None = None, *, start_background: bool = True) -> FastAPI:
    env = env or get_env_config()
    env.ensure_dirs()
    setup_logging(env.log_level, env.log_dir)
    run_migrations(env.database_url)
    services = build_services(env)
    _normalize_settings(services)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        services.bus.bind(loop)
        services.providers.write_cookie_files()
        if start_background:
            await services.scan_manager.start()
            await services.downloads.start()
            await services.notifications.start()
            services.scheduler.start()
            asyncio.create_task(_initial_session_check(services))
        log.info("Creeparr %s ready on port %s", __version__, env.port)
        try:
            yield
        finally:
            if start_background:
                services.scheduler.shutdown()
                await services.notifications.stop()
                await services.scan_manager.stop()
                await services.downloads.stop()
            await services.providers.aclose_all()
            services.engine.dispose()

    app = FastAPI(title="Creeparr", version=__version__, lifespan=lifespan, docs_url="/api/docs")
    app.state.services = services

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
        )

    @app.exception_handler(ProviderError)
    async def _provider_error(_: Request, exc: ProviderError):
        return JSONResponse(
            status_code=502,
            content={"error": {"code": exc.code, "message": str(exc), "detail": exc.detail}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_failed",
                    "message": "request validation failed",
                    "detail": str(exc.errors()),
                }
            },
        )

    from starlette.middleware.base import BaseHTTPMiddleware

    from creeparr.api.auth import OPEN_PATHS, auth_active, is_authenticated
    from creeparr.api.guard import (
        CSRF_HEADER,
        is_allowed_host,
        needs_csrf_header,
        parse_allowed_hosts,
    )

    allowed_hosts = parse_allowed_hosts(env.allowed_hosts)

    def _forbidden(code: str, message: str) -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": {"code": code, "message": message}})

    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if (
                path != "/health"
                and not auth_active(services)
                and not is_allowed_host(request.headers.get("host"), allowed_hosts)
            ):
                return _forbidden(
                    "host_not_allowed",
                    "This host name is not allowed while Creeparr has no password. Open it "
                    "by IP address and set a password in Settings → Security, or add the "
                    "name to CREEPARR_ALLOWED_HOSTS.",
                )
            if needs_csrf_header(request.method, path) and not request.headers.get(CSRF_HEADER):
                return _forbidden(
                    "csrf_header_missing",
                    f"state-changing API requests must send the {CSRF_HEADER} header",
                )
            # /api/docs and /openapi.json describe every route; keep them behind login too.
            gated = (path.startswith("/api/") or path == "/openapi.json") and path not in OPEN_PATHS
            if gated and not is_authenticated(request, services):
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {"code": "unauthorized", "message": "authentication required"}
                    },
                )
            return await call_next(request)

    app.add_middleware(AuthMiddleware)
    app.include_router(api_router)

    @app.get("/health", include_in_schema=False)
    def health():
        payload, code = health_payload(services)
        return JSONResponse(payload, status_code=code)

    _mount_spa(app, env.resolved_static_dir)
    return app


def _normalize_settings(services: Services) -> None:
    """One-time cleanup of retired defaults stored in the DB."""
    from creeparr.providers.onlyfans.provider import DEAD_RULES_URLS, DEFAULT_RULES_URL

    of = services.settings.get().onlyfans
    if of.dynamic_rules_url.strip() in DEAD_RULES_URLS:
        services.settings.update({"onlyfans": {"dynamic_rules_url": DEFAULT_RULES_URL}})
        log.info("upgraded stored OnlyFans rules URL to the current default")


async def _initial_session_check(services: Services) -> None:
    for provider in services.providers:
        try:
            await provider.check_session()
        except Exception:  # noqa: BLE001
            log.exception("initial %s session check failed", provider.name)


_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "not_found", "message": "no such endpoint"}},
            )
        candidate = static_dir / path
        if path and candidate.is_file() and static_dir.resolve() in candidate.resolve().parents:
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, headers=_NO_CACHE)
        return JSONResponse(
            status_code=200,
            content={
                "app": "creeparr",
                "version": __version__,
                "note": "UI not built; API at /api/v1",
            },
        )

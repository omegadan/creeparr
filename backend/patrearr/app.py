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

from patrearr import __version__
from patrearr.api.router import api_router
from patrearr.api.system import health_payload
from patrearr.config import EnvConfig, get_env_config
from patrearr.core.errors import AppError
from patrearr.db.migrate import run_migrations
from patrearr.logging_setup import setup_logging
from patrearr.patreon.errors import PatreonError
from patrearr.services import Services, build_services

log = logging.getLogger(__name__)


def create_app(env: EnvConfig | None = None, *, start_background: bool = True) -> FastAPI:
    env = env or get_env_config()
    env.ensure_dirs()
    setup_logging(env.log_level, env.log_dir)
    run_migrations(env.database_url)
    services = build_services(env)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        services.bus.bind(loop)
        services.patreon.write_cookie_file()
        if start_background:
            await services.scan_manager.start()
            await services.downloads.start()
            services.scheduler.start()
            asyncio.create_task(_initial_session_check(services))
        log.info("Patrearr %s ready on port %s", __version__, env.port)
        try:
            yield
        finally:
            if start_background:
                services.scheduler.shutdown()
                await services.scan_manager.stop()
                await services.downloads.stop()
            await services.patreon.aclose()
            services.engine.dispose()

    app = FastAPI(title="Patrearr", version=__version__, lifespan=lifespan, docs_url="/api/docs")
    app.state.services = services

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
        )

    @app.exception_handler(PatreonError)
    async def _patreon_error(_: Request, exc: PatreonError):
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

    app.include_router(api_router)

    @app.get("/health", include_in_schema=False)
    def health():
        payload, code = health_payload(services)
        return JSONResponse(payload, status_code=code)

    _mount_spa(app, env.resolved_static_dir)
    return app


async def _initial_session_check(services: Services) -> None:
    try:
        await services.patreon.check_session()
    except Exception:  # noqa: BLE001
        log.exception("initial session check failed")


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
            return FileResponse(index)
        return JSONResponse(
            status_code=200,
            content={
                "app": "patrearr",
                "version": __version__,
                "note": "UI not built; API at /api/v1",
            },
        )

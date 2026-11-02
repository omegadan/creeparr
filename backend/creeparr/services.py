"""Composition root: builds and holds all long-lived components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.engine import Engine

from creeparr.config import EnvConfig
from creeparr.core.events import EventBus
from creeparr.core.lifecycle import MediaLifecycle
from creeparr.core.notifications import NotificationService
from creeparr.core.security import get_secret_key
from creeparr.core.settings_service import SettingsService
from creeparr.db.engine import SessionFactory, make_engine, make_session_factory
from creeparr.downloader.claims import Claims
from creeparr.downloader.embed import Embedder
from creeparr.downloader.handlers.direct import DirectHandler
from creeparr.downloader.handlers.ytdlp import YtDlpHandler
from creeparr.downloader.library import Library
from creeparr.downloader.longwork import LongWork
from creeparr.downloader.manager import DownloadManager
from creeparr.downloader.metadata import SystemFfmpeg
from creeparr.downloader.progress import JobProgress
from creeparr.downloader.run import DownloadRun
from creeparr.providers.catalog import PROVIDER_TYPES
from creeparr.providers.registry import ProviderRegistry
from creeparr.scanner.scan_manager import ScanManager
from creeparr.scanner.scanner import Scanner
from creeparr.scheduler import SchedulerService


@dataclass
class Services:
    env: EnvConfig
    engine: Engine
    session_factory: SessionFactory
    settings: SettingsService
    bus: EventBus
    providers: ProviderRegistry
    lifecycle: MediaLifecycle
    scanner: Scanner
    scan_manager: ScanManager
    claims: Claims
    downloads: DownloadManager
    library: Library
    notifications: NotificationService
    auth_secret: bytes = b""
    scheduler: SchedulerService = field(init=False)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.scheduler = SchedulerService(self)


def build_services(env: EnvConfig) -> Services:
    engine = make_engine(env.database_url)
    factory = make_session_factory(engine)
    settings = SettingsService(factory)
    bus = EventBus()
    providers = ProviderRegistry([cls(env, settings, factory, bus) for cls in PROVIDER_TYPES])
    lifecycle = MediaLifecycle(factory, bus, settings)
    scanner = Scanner(factory, settings, bus, providers, lifecycle)
    scan_manager = ScanManager(scanner, factory, providers, bus)
    long_work = LongWork()
    ffmpeg = SystemFfmpeg(env)
    embedder = Embedder(factory, settings, providers, ffmpeg, long_work)
    claims = Claims(factory, settings, providers, lifecycle)
    run = DownloadRun(
        env,
        factory,
        settings,
        providers,
        lifecycle,
        direct=DirectHandler(settings, ffmpeg, long_work),
        ytdlp=YtDlpHandler(settings, ffmpeg, long_work),
        embedder=embedder,
        long_work=long_work,
    )
    downloads = DownloadManager(
        env, factory, settings, bus, lifecycle, claims, run, JobProgress(factory, bus)
    )
    lifecycle.workers = downloads  # cancels running downloads, wakes workers for new jobs
    library = Library(env, factory, settings, bus, lifecycle, embedder)
    notifications = NotificationService(settings, bus)
    return Services(
        env=env,
        engine=engine,
        session_factory=factory,
        settings=settings,
        bus=bus,
        providers=providers,
        lifecycle=lifecycle,
        scanner=scanner,
        scan_manager=scan_manager,
        claims=claims,
        downloads=downloads,
        library=library,
        notifications=notifications,
        auth_secret=get_secret_key(env.config_dir),
    )

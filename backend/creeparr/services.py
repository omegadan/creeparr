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
from creeparr.downloader.manager import DownloadManager
from creeparr.providers.instagram.provider import InstagramProvider
from creeparr.providers.onlyfans.provider import OnlyFansProvider
from creeparr.providers.patreon import PatreonProvider
from creeparr.providers.reddit.provider import RedditProvider
from creeparr.providers.registry import ProviderRegistry
from creeparr.providers.youtube import YouTubeProvider
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
    downloads: DownloadManager
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
    providers = ProviderRegistry(
        [
            PatreonProvider(env, settings, factory, bus),
            OnlyFansProvider(env, settings, factory, bus),
            YouTubeProvider(env, settings, factory, bus),
            InstagramProvider(env, settings, factory, bus),
            RedditProvider(env, settings, factory, bus),
        ]
    )
    lifecycle = MediaLifecycle(factory, bus)
    scanner = Scanner(factory, settings, bus, providers, lifecycle)
    scan_manager = ScanManager(scanner, factory, providers, bus)
    downloads = DownloadManager(env, factory, settings, bus, providers, lifecycle)
    lifecycle.workers = downloads  # cancels running downloads, wakes workers for new jobs
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
        downloads=downloads,
        notifications=notifications,
        auth_secret=get_secret_key(env.config_dir),
    )

"""Composition root: builds and holds all long-lived components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.engine import Engine

from patrearr.config import EnvConfig
from patrearr.core.events import EventBus
from patrearr.core.notifications import NotificationService
from patrearr.core.settings_service import SettingsService
from patrearr.db.engine import SessionFactory, make_engine, make_session_factory
from patrearr.downloader.manager import DownloadManager
from patrearr.providers.onlyfans.provider import OnlyFansProvider
from patrearr.providers.patreon import PatreonProvider
from patrearr.providers.registry import ProviderRegistry
from patrearr.scanner.scan_manager import ScanManager
from patrearr.scanner.scanner import Scanner
from patrearr.scheduler import SchedulerService


@dataclass
class Services:
    env: EnvConfig
    engine: Engine
    session_factory: SessionFactory
    settings: SettingsService
    bus: EventBus
    providers: ProviderRegistry
    scanner: Scanner
    scan_manager: ScanManager
    downloads: DownloadManager
    notifications: NotificationService
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
        ]
    )
    scanner = Scanner(factory, settings, bus, providers)
    scan_manager = ScanManager(scanner, factory, providers, bus)
    downloads = DownloadManager(env, factory, settings, bus, providers)
    notifications = NotificationService(settings, bus)
    return Services(
        env=env,
        engine=engine,
        session_factory=factory,
        settings=settings,
        bus=bus,
        providers=providers,
        scanner=scanner,
        scan_manager=scan_manager,
        downloads=downloads,
        notifications=notifications,
    )

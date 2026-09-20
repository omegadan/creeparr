"""Composition root: builds and holds all long-lived components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.engine import Engine

from patreonarr.config import EnvConfig
from patreonarr.core.events import EventBus
from patreonarr.core.patreon_service import PatreonService
from patreonarr.core.settings_service import SettingsService
from patreonarr.db.engine import SessionFactory, make_engine, make_session_factory
from patreonarr.downloader.manager import DownloadManager
from patreonarr.scanner.scan_manager import ScanManager
from patreonarr.scanner.scanner import Scanner
from patreonarr.scheduler import SchedulerService


@dataclass
class Services:
    env: EnvConfig
    engine: Engine
    session_factory: SessionFactory
    settings: SettingsService
    bus: EventBus
    patreon: PatreonService
    scanner: Scanner
    scan_manager: ScanManager
    downloads: DownloadManager
    scheduler: SchedulerService = field(init=False)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.scheduler = SchedulerService(self)


def build_services(env: EnvConfig) -> Services:
    engine = make_engine(env.database_url)
    factory = make_session_factory(engine)
    settings = SettingsService(factory)
    bus = EventBus()
    patreon = PatreonService(env, settings, factory, bus)
    scanner = Scanner(factory, settings, bus, patreon)
    scan_manager = ScanManager(scanner, factory, patreon, bus)
    downloads = DownloadManager(env, factory, settings, bus, patreon)
    return Services(
        env=env,
        engine=engine,
        session_factory=factory,
        settings=settings,
        bus=bus,
        patreon=patreon,
        scanner=scanner,
        scan_manager=scan_manager,
        downloads=downloads,
    )

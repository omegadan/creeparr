"""Run Alembic migrations programmatically at startup."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
log = logging.getLogger(__name__)


def alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def run_migrations(database_url: str) -> None:
    log.info("Applying database migrations")
    command.upgrade(alembic_config(database_url), "head")

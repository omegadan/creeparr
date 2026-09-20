"""Environment-level configuration.

Only things that must be known before the database exists live here (paths, port,
log level). Everything else is a user setting stored in the DB; see
``patreonarr.core.settings_service``.
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PATREONARR_", extra="ignore")

    config_dir: Path = Field(default=Path("/config"))
    download_dir: Path = Field(default=Path("/downloads"))
    host: str = "0.0.0.0"
    port: int = 7979
    log_level: str = "INFO"
    db_path: Path | None = None
    static_dir: Path | None = None
    ffmpeg_path: str | None = None

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def database_path(self) -> Path:
        return self.db_path or (self.config_dir / "patreonarr.db")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    @property
    def log_dir(self) -> Path:
        return self.config_dir / "logs"

    @property
    def cookies_dir(self) -> Path:
        return self.config_dir / "cookies"

    @property
    def cookies_file(self) -> Path:
        return self.cookies_dir / "patreon.txt"

    @property
    def resolved_static_dir(self) -> Path:
        return self.static_dir or (Path(__file__).parent / "static")

    def resolve_ffmpeg(self) -> str | None:
        if self.ffmpeg_path:
            return self.ffmpeg_path
        return shutil.which("ffmpeg")

    def ensure_dirs(self) -> None:
        for d in (self.config_dir, self.log_dir, self.cookies_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_env_config() -> EnvConfig:
    return EnvConfig()

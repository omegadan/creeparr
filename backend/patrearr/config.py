"""Environment-level configuration.

Only things that must be known before the database exists live here (paths, port,
log level). Everything else is a user setting stored in the DB; see
``patrearr.core.settings_service``.
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PATREARR_", extra="ignore")

    config_dir: Path = Field(default=Path("/config"))
    download_dir: Path = Field(default=Path("/downloads"))
    onlyfans_download_dir: Path | None = None
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
        return self.db_path or (self.config_dir / "patrearr.db")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    def download_root(self, provider: str) -> Path:
        """Base directory for a provider's archive. OnlyFans can use its own."""
        if provider == "onlyfans" and self.onlyfans_download_dir is not None:
            return self.onlyfans_download_dir
        return self.download_dir

    def download_roots(self) -> dict[str, Path]:
        """Distinct download roots, keyed by a label (for disk checks / status)."""
        roots = {"downloads": self.download_dir}
        if (
            self.onlyfans_download_dir is not None
            and self.onlyfans_download_dir != self.download_dir
        ):
            roots["onlyfans"] = self.onlyfans_download_dir
        return roots

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
        for root in self.download_roots().values():
            root.mkdir(parents=True, exist_ok=True)
        self._adopt_legacy_database()

    def _adopt_legacy_database(self) -> None:
        """Pick up a database created under the project's old name (patreonarr)."""
        if self.db_path is not None or self.database_path.exists():
            return
        legacy = self.config_dir / "patreonarr.db"
        if not legacy.exists():
            return
        for suffix in ("", "-wal", "-shm"):
            src = legacy.with_name(legacy.name + suffix)
            if src.exists():
                src.rename(self.database_path.with_name(self.database_path.name + suffix))


@lru_cache(maxsize=1)
def get_env_config() -> EnvConfig:
    return EnvConfig()

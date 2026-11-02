"""Environment-level configuration.

Only things that must be known before the database exists live here (paths, port,
log level). Everything else is a user setting stored in the DB; see
``creeparr.core.settings_service``.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# A provider's own archive root. Any provider without one uses ``CREEPARR_DOWNLOAD_DIR``.
_PROVIDER_DIR_VARIABLE = re.compile(r"CREEPARR_([A-Z0-9]+)_DOWNLOAD_DIR")


class EnvConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CREEPARR_", extra="ignore")

    config_dir: Path = Field(default=Path("/config"))
    # Default archive root, used by every provider that has no directory of its own.
    download_dir: Path = Field(default=Path("/downloads"))
    #: Providers' own archive roots, by provider name, read from
    #: ``CREEPARR_<PROVIDER>_DOWNLOAD_DIR`` unless given.
    provider_dirs: dict[str, Path] = Field(default_factory=dict)
    host: str = "0.0.0.0"
    port: int = 7979
    # Extra Host names accepted while no UI password is set (see creeparr.api.guard).
    allowed_hosts: str = ""
    log_level: str = "INFO"
    db_path: Path | None = None
    static_dir: Path | None = None
    ffmpeg_path: str | None = None

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("db_path", "static_dir", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        """``CREEPARR_X_DOWNLOAD_DIR=`` (empty) means "not set", not the current dir.

        docker-compose emits an empty value when the matching host variable is
        absent, and Unraid does the same for a blanked-out field.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def _read_provider_dirs(self) -> EnvConfig:
        if not self.provider_dirs:
            for variable, value in os.environ.items():
                match = _PROVIDER_DIR_VARIABLE.fullmatch(variable)
                if match and value.strip():  # blank means unset, as above
                    self.provider_dirs[match.group(1).lower()] = Path(value)
        return self

    @property
    def database_path(self) -> Path:
        return self.db_path or (self.config_dir / "creeparr.db")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    def download_root(self, provider: str) -> Path:
        """Base directory for a provider's archive.

        ``CREEPARR_<PROVIDER>_DOWNLOAD_DIR`` when set, otherwise ``CREEPARR_DOWNLOAD_DIR``.
        """
        return self.provider_dirs.get(provider, self.download_dir)

    def download_roots(self) -> dict[str, Path]:
        """Distinct download roots, keyed by a label (for disk checks / status).

        The default root is always present under ``"downloads"``; a provider
        appears under its own name only when it points somewhere else.
        """
        roots = {"downloads": self.download_dir}
        for provider, path in self.provider_dirs.items():
            if path != self.download_dir and path not in roots.values():
                roots[provider] = path
        return roots

    def describe_paths(self, providers: Iterable[str]) -> dict[str, str | None]:
        """Path summary for the settings / status API: `<provider>_download_dir` for each
        of `providers` (None = uses the default root)."""
        out: dict[str, str | None] = {
            "config_dir": str(self.config_dir),
            "download_dir": str(self.download_dir),
        }
        for provider in providers:
            path = self.provider_dirs.get(provider)
            out[f"{provider}_download_dir"] = str(path) if path is not None else None
        return out

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
        """Pick up a database created under one of the project's earlier names.

        The app was called "patreonarr", then "patrearr", before "creeparr". An
        existing install keeps its data: the first legacy file found is renamed
        (with its -wal/-shm companions) to the current name.
        """
        if self.db_path is not None or self.database_path.exists():
            return
        for old_name in ("patrearr.db", "patreonarr.db"):
            legacy = self.config_dir / old_name
            if not legacy.exists():
                continue
            for suffix in ("", "-wal", "-shm"):
                src = legacy.with_name(legacy.name + suffix)
                if src.exists():
                    src.rename(self.database_path.with_name(self.database_path.name + suffix))
            return


# Env vars from the previous name keep working so existing installs (Unraid
# templates, compose files) need no changes when upgrading.
LEGACY_ENV_PREFIX = "PATREARR_"


def _adopt_legacy_env() -> None:
    prefix = EnvConfig.model_config.get("env_prefix", "")
    for key, value in list(os.environ.items()):
        if key.startswith(LEGACY_ENV_PREFIX):
            os.environ.setdefault(prefix + key[len(LEGACY_ENV_PREFIX) :], value)


@lru_cache(maxsize=1)
def get_env_config() -> EnvConfig:
    _adopt_legacy_env()
    return EnvConfig()

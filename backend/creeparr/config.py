"""Environment-level configuration.

Only things that must be known before the database exists live here (paths, port,
log level). Everything else is a user setting stored in the DB; see
``creeparr.core.settings_service``.
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Providers that may have their own archive root (``CREEPARR_<PROVIDER>_DOWNLOAD_DIR``).
# Any provider without one falls back to ``CREEPARR_DOWNLOAD_DIR``.
PROVIDERS_WITH_DOWNLOAD_DIR: tuple[str, ...] = (
    "patreon",
    "onlyfans",
    "youtube",
    "instagram",
    "reddit",
)


class EnvConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CREEPARR_", extra="ignore")

    config_dir: Path = Field(default=Path("/config"))
    # Default archive root, used by every provider that has no directory of its own.
    download_dir: Path = Field(default=Path("/downloads"))
    patreon_download_dir: Path | None = None
    onlyfans_download_dir: Path | None = None
    youtube_download_dir: Path | None = None
    instagram_download_dir: Path | None = None
    reddit_download_dir: Path | None = None
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

    @field_validator(
        *(f"{p}_download_dir" for p in PROVIDERS_WITH_DOWNLOAD_DIR),
        "db_path",
        "static_dir",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        """``CREEPARR_X_DOWNLOAD_DIR=`` (empty) means "not set", not the current dir.

        docker-compose emits an empty value when the matching host variable is
        absent, and Unraid does the same for a blanked-out field.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def database_path(self) -> Path:
        return self.db_path or (self.config_dir / "creeparr.db")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    def provider_download_dirs(self) -> dict[str, Path | None]:
        """Each provider's own archive root, or None where it uses ``download_dir``."""
        return {p: getattr(self, f"{p}_download_dir") for p in PROVIDERS_WITH_DOWNLOAD_DIR}

    def download_root(self, provider: str) -> Path:
        """Base directory for a provider's archive.

        ``CREEPARR_<PROVIDER>_DOWNLOAD_DIR`` when set, otherwise ``CREEPARR_DOWNLOAD_DIR``.
        """
        own = getattr(self, f"{provider}_download_dir", None)
        return own if own is not None else self.download_dir

    def download_roots(self) -> dict[str, Path]:
        """Distinct download roots, keyed by a label (for disk checks / status).

        The default root is always present under ``"downloads"``; a provider
        appears under its own name only when it points somewhere else.
        """
        roots = {"downloads": self.download_dir}
        for provider, path in self.provider_download_dirs().items():
            if path is not None and path != self.download_dir and path not in roots.values():
                roots[provider] = path
        return roots

    def describe_paths(self) -> dict[str, str | None]:
        """Path summary for the settings / status API (None = uses the default root)."""
        out: dict[str, str | None] = {
            "config_dir": str(self.config_dir),
            "download_dir": str(self.download_dir),
        }
        for provider, path in self.provider_download_dirs().items():
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

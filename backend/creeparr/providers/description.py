"""What Creeparr knows about a provider without asking it: plain data, served to the UI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from creeparr.db.enums import MediaSource


@dataclass(frozen=True)
class CredentialField:
    """One account field the auth endpoints accept, as the account form shows it."""

    #: The setting's name in the provider's settings group.
    key: str
    label: str
    hint: str = ""
    optional: bool = False
    multiline: bool = False


#: The optional full cookie export most providers accept.
COOKIES_TXT = CredentialField(
    "cookies_txt",
    "cookies.txt (optional)",
    "Full Netscape cookie export; extra cookies (cf_clearance) are forwarded",
    optional=True,
    multiline=True,
)


@dataclass(frozen=True)
class ProviderDescription:
    #: Also the provider's settings group and `creators.provider`.
    name: str
    label: str
    #: Shown above the account form: what the provider reads, and what it needs.
    intro: str
    credentials: tuple[CredentialField, ...] = ()
    #: Every post type its scans store, with how the UI names it.
    post_types: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    #: The container the "auto" setting means for this provider's creators' videos.
    video_container: str = "mp4"
    #: Embeds this provider fetches with its own session, whoever posted them.
    hosts: frozenset[MediaSource] = frozenset()
    #: What to do when one of its videos asks for a signed-in session.
    sign_in_help: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "post_types", MappingProxyType(dict(self.post_types)))
        if not self.sign_in_help:
            help_ = f"Check the {self.label} session in Settings -> Accounts -> {self.label}"
            object.__setattr__(self, "sign_in_help", help_)

    @property
    def credential_keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.credentials)

    @property
    def download_dir_variable(self) -> str:
        """The environment variable naming this provider's own archive root."""
        return f"CREEPARR_{self.name.upper()}_DOWNLOAD_DIR"

    def as_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "intro": self.intro,
            "credentials": [
                {
                    "key": c.key,
                    "label": c.label,
                    "hint": c.hint,
                    "optional": c.optional,
                    "multiline": c.multiline,
                }
                for c in self.credentials
            ],
            "post_types": dict(self.post_types),
            "download_dir_variable": self.download_dir_variable,
        }

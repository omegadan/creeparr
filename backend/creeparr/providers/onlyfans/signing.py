"""OnlyFans request signing.

OnlyFans' private API requires a per-request `sign` header derived from a set of
"dynamic rules" that the OnlyFans web app ships and that change every few weeks.
The community keeps the current rules in a JSON file; we fetch and cache it. The
algorithm below is the long-standing DIGITALCRIMINALs / onlyfans-dynamic-rules one.
"""

from __future__ import annotations

import hashlib
import string
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass
class DynamicRules:
    static_param: str
    format: str
    checksum_indexes: list[int]
    checksum_constant: int
    app_token: str
    remove_headers: list[str]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DynamicRules:
        """Rules from the (third-party) rules URL; ValueError if they look wrong."""
        return cls(
            static_param=data["static_param"],
            format=_checked_format(data["format"]),
            checksum_indexes=_checked_indexes(data["checksum_indexes"]),
            checksum_constant=int(data["checksum_constant"]),
            app_token=data.get("app_token") or data.get("app-token", ""),
            remove_headers=list(data.get("remove_headers", [])),
        )


_SHA1_HEX_LENGTH = 40


def _checked_format(fmt: Any) -> str:
    """The sign format may only place the hash and the checksum, e.g. `1:{}:{:x}:2`.

    It's passed to str.format, which can also reach attributes and items of its
    arguments; a rules file from someone else gets no more than filling in.
    """
    if not isinstance(fmt, str):
        raise ValueError("rules format is not a string")
    try:
        fields = [p for p in string.Formatter().parse(fmt) if p[1] is not None]
        for _, field, spec, conversion in fields:
            if field not in ("", "0", "1") or conversion or "{" in (spec or ""):
                raise ValueError
        if not fields:
            raise ValueError
        fmt.format("0" * _SHA1_HEX_LENGTH, 0)  # e.g. mixed {} and {0} numbering
    except (ValueError, IndexError, KeyError):
        raise ValueError(f"rules format {fmt!r} may only contain {{}} fields") from None
    return fmt


def _checked_indexes(indexes: Any) -> list[int]:
    if not isinstance(indexes, list) or not all(
        type(i) is int and 0 <= i < _SHA1_HEX_LENGTH for i in indexes
    ):
        raise ValueError(f"rules checksum_indexes {indexes!r} must be positions in the hash")
    return indexes


def sign_request(
    url: str, rules: DynamicRules, user_id: str, now: int | None = None
) -> dict[str, str]:
    """Return the `sign` and `time` header values for a GET to `url`."""
    ts = str(now if now is not None else int(round(time.time() * 1000)))
    parsed = urlparse(url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    message = "\n".join([rules.static_param, ts, path, user_id or "0"])
    sha1 = hashlib.sha1(message.encode("utf-8")).hexdigest()  # noqa: S324 - required by OF
    ascii_hash = sha1.encode("ascii")
    checksum = sum(ascii_hash[i] for i in rules.checksum_indexes) + rules.checksum_constant
    sign = rules.format.format(sha1, abs(checksum))
    return {"sign": sign, "time": ts}

"""OnlyFans request signing.

OnlyFans' private API requires a per-request `sign` header derived from a set of
"dynamic rules" that the OnlyFans web app ships and that change every few weeks.
The community keeps the current rules in a JSON file; we fetch and cache it. The
algorithm below is the long-standing DIGITALCRIMINALs / onlyfans-dynamic-rules one.
"""

from __future__ import annotations

import hashlib
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
        return cls(
            static_param=data["static_param"],
            format=data["format"],
            checksum_indexes=list(data["checksum_indexes"]),
            checksum_constant=int(data["checksum_constant"]),
            app_token=data.get("app_token") or data.get("app-token", ""),
            remove_headers=list(data.get("remove_headers", [])),
        )


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

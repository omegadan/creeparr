from __future__ import annotations

import os
from pathlib import Path

from patrearr.patreon.cookies import CookieSet, parse_netscape

COOKIES_TXT = """# Netscape HTTP Cookie File
.patreon.com\tTRUE\t/\tTRUE\t1900000000\tsession_id\tfrom-file
#HttpOnly_.patreon.com\tTRUE\t/\tTRUE\t1900000000\tcf_clearance\tcf-value
.example.com\tTRUE\t/\tFALSE\t0\tother\tnope
"""


def test_parse_netscape():
    cookies = parse_netscape(COOKIES_TXT)
    assert [c.name for c in cookies] == ["session_id", "cf_clearance", "other"]
    assert cookies[1].value == "cf-value"


def test_from_settings_precedence_and_header():
    cs = CookieSet.from_settings("explicit", COOKIES_TXT)
    assert cs.session_id == "explicit"
    assert cs.extra == {"cf_clearance": "cf-value"}
    assert cs.header() == "cf_clearance=cf-value; session_id=explicit"
    cs2 = CookieSet.from_settings("", COOKIES_TXT)
    assert cs2.session_id == "from-file"
    assert not CookieSet.from_settings("", "").is_configured


def test_write_netscape(tmp_path: Path):
    target = tmp_path / "cookies" / "patreon.txt"
    CookieSet(session_id="abc", extra={"cf_clearance": "z"}).write_netscape(target)
    text = target.read_text()
    assert "\tsession_id\tabc" in text and "\tcf_clearance\tz" in text
    assert oct(os.stat(target).st_mode & 0o777) == "0o600"

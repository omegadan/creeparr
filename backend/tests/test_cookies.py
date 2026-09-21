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


YOUTUBE_COOKIES = """# Netscape HTTP Cookie File
.youtube.com\tTRUE\t/\tTRUE\t1900000000\tLOGIN_INFO\tyt-login-token
.google.com\tTRUE\t/\tTRUE\t1900000000\tSID\tgoogle-sid
#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1900000000\tSAPISID\tsapi-value
"""

# Some browser extensions emit space-separated columns rather than tabs.
YOUTUBE_COOKIES_SPACES = (
    "# Netscape HTTP Cookie File\n.youtube.com TRUE / TRUE 1900000000 LOGIN_INFO yt-login-token\n"
)


def test_write_cookiefile_preserves_youtube_domains(tmp_path: Path):
    from patrearr.patreon.cookies import write_cookiefile

    target = tmp_path / "cookies" / "youtube.txt"
    n = write_cookiefile(YOUTUBE_COOKIES, target)
    assert n == 3
    text = target.read_text()
    # Every cookie keeps its own domain; nothing is stamped as patreon.com.
    assert "patreon.com" not in text
    assert "\t.youtube.com\t" not in text  # domain is the first column, not mid-line
    assert text.splitlines()[0] == "# Netscape HTTP Cookie File"
    assert ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tLOGIN_INFO\tyt-login-token" in text
    assert ".google.com\tTRUE\t/\tTRUE\t1900000000\tSID\tgoogle-sid" in text
    assert ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSAPISID\tsapi-value" in text
    assert oct(os.stat(target).st_mode & 0o777) == "0o600"


def test_write_cookiefile_normalises_spaces_to_tabs(tmp_path: Path):
    from patrearr.patreon.cookies import write_cookiefile

    target = tmp_path / "youtube.txt"
    assert write_cookiefile(YOUTUBE_COOKIES_SPACES, target) == 1
    assert (
        ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tLOGIN_INFO\tyt-login-token" in target.read_text()
    )


def test_write_cookiefile_empty_returns_zero(tmp_path: Path):
    from patrearr.patreon.cookies import write_cookiefile

    target = tmp_path / "youtube.txt"
    assert write_cookiefile("# Netscape HTTP Cookie File\n\n", target) == 0
    assert not target.exists()


def test_cookieset_domain_is_configurable(tmp_path: Path):
    target = tmp_path / "instagram.txt"
    CookieSet(session_id=None, extra={"sessionid": "abc"}, domain="instagram.com").write_netscape(
        target
    )
    text = target.read_text()
    assert ".instagram.com\tTRUE\t/\tTRUE\t" in text
    assert "patreon.com" not in text

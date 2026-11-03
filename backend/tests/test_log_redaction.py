"""Logs can end up pasted into a public issue, so credentials must never reach them."""

from __future__ import annotations

import logging

import pytest

from creeparr.logging_setup import RedactFilter


def redacted(msg: str, *args: object) -> str:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)
    RedactFilter().filter(record)
    return record.getMessage()


@pytest.mark.parametrize(
    "line",
    [
        "Cookie: session_id=SECRETVALUE; other=1",  # Patreon
        "cookies sess=SECRETVALUE; auth_id=12345",  # OnlyFans
        "headers {'x-bc': 'SECRETVALUE', 'user-id': '1'}",
        'request failed: {"x_bc": "SECRETVALUE"}',
        "cf_clearance=SECRETVALUE; __cf_bm=x",
        "sessionid=SECRETVALUE",  # Instagram
        "reddit_session=SECRETVALUE",
        "__Secure-3PSID=SECRETVALUE; SAPISID=SECRETVALUE",  # YouTube
        ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tSECRETVALUE",  # cookies.txt line
        "Authorization: Bearer SECRETVALUE",
    ],
)
def test_credentials_are_masked(line):
    assert "SECRETVALUE" not in redacted(line)


def test_masking_applies_to_formatted_arguments():
    assert "SECRETVALUE" not in redacted("sent %s", "sess=SECRETVALUE")


def test_ordinary_lines_are_left_alone():
    line = "scan of Blender Studio finished: 12 new posts, session expired? no"
    assert redacted(line) == line

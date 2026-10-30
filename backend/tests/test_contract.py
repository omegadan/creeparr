"""The wire contract: every response and event is declared, the UI's generated types match the
declarations, and the actions offered on posts and media are the lifecycle's."""

from __future__ import annotations

import json

import pytest

from creeparr import __version__
from creeparr.api.contract import openapi, schema_ts, typescript
from creeparr.api.system import hello
from creeparr.core.lifecycle import ItemAction, PostAction, item_actions, post_actions
from creeparr.core.server_events import EVENTS, ServerEvent
from creeparr.db.enums import MediaStatus, PostStatus
from creeparr.services import build_services

# Routes whose body isn't a declared JSON shape.
UNDECLARED = {
    ("get", "/api/v1/events"),  # the server-sent event stream; its events are declared
    ("get", "/api/v1/posts/{post_id}/raw"),  # the provider's own JSON, as stored
    ("get", "/api/v1/media/{media_id}/file"),  # a downloaded file
}


def test_the_ui_types_are_generated_from_the_current_declarations():
    assert schema_ts().read_text() == typescript(openapi()), (
        "frontend/src/api/schema.ts is stale: run `make api-types`"
    )


def test_every_json_route_declares_its_response():
    undeclared = set()
    for path, ops in openapi()["paths"].items():
        for method, op in ops.items():
            for code, response in op["responses"].items():
                if not code.startswith("2") or "content" not in response:
                    continue
                schema = response["content"]["application/json"]["schema"]
                if "$ref" not in json.dumps(schema):
                    undeclared.add((method, path))
    assert undeclared == UNDECLARED


def test_every_event_is_in_the_contract():
    spec = openapi()
    assert spec["x-server-events"].keys() == EVENTS.keys()
    assert len(EVENTS) == 16
    for ref in spec["x-server-events"].values():
        assert ref.removeprefix("#/components/schemas/") in spec["components"]["schemas"]


def test_two_events_cant_share_a_name():
    with pytest.raises(ValueError, match="two events are named job.started"):

        class Again(ServerEvent):
            event = "job.started"


def test_only_what_was_set_is_sent():
    # The UI merges job.progress into the cached job; a null would wipe a known value.
    progress = EVENTS["job.progress"](id=1, bytes_downloaded=10)
    assert progress.payload() == {"id": 1, "bytes_downloaded": 10}


def test_the_hello_event_names_the_servers_version(env, engine):
    event = hello(build_services(env))
    assert event.version == __version__
    assert set(event.providers) == {"patreon", "onlyfans", "youtube", "instagram", "reddit"}


# ---- the actions offered ---------------------------------------------------------------

S = MediaStatus
I = ItemAction  # noqa: E741

# As the UI offered them before the backend did: retry for what can be fetched again,
# re-download for what's archived (or was), unskip for what isn't wanted, skip otherwise
# unless it's archived.
ITEM_ACTIONS = {
    S.DISCOVERED: [I.RETRY, I.SKIP],
    S.QUEUED: [I.SKIP],
    S.DOWNLOADING: [I.SKIP],
    S.COMPLETED: [I.REDOWNLOAD],
    S.FAILED: [I.RETRY, I.SKIP],
    S.FAILED_PERMANENT: [I.RETRY, I.SKIP],
    S.CANCELLED: [I.RETRY, I.SKIP],
    S.SKIPPED: [I.RETRY, I.UNSKIP],
    S.UNSUPPORTED: [I.RETRY, I.SKIP],
    S.UNSUPPORTED_DRM: [I.RETRY, I.SKIP],
    S.NO_ACCESS: [I.SKIP],
    S.MISSING: [I.REDOWNLOAD, I.SKIP],
}


@pytest.mark.parametrize("status", list(MediaStatus))
def test_the_item_actions_for_a_wanted_item(status):
    assert item_actions(status, wanted=True) == ITEM_ACTIONS[status]


@pytest.mark.parametrize("status", list(MediaStatus))
def test_an_unwanted_item_offers_unskip_instead_of_skip(status):
    expected = [a for a in ITEM_ACTIONS[status] if a not in (I.SKIP, I.UNSKIP)] + [I.UNSKIP]
    assert item_actions(status, wanted=False) == expected


@pytest.mark.parametrize("status", list(PostStatus))
def test_the_post_actions(status):
    download = PostAction.FORCE_DOWNLOAD if status == PostStatus.COMPLETED else PostAction.DOWNLOAD
    skip = PostAction.UNSKIP if status == PostStatus.SKIPPED else PostAction.SKIP
    assert post_actions(status) == [download, skip]

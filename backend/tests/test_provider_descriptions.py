"""Every registered provider is described once, and its description matches the code: its
settings group, its account fields, its secrets and the post types its parsers store."""

from __future__ import annotations

import pytest

from creeparr.core.settings_service import AppSettings, secret_fields
from creeparr.patreon.parsing import IncludedIndex, post_from_resource
from creeparr.providers.instagram.client import SOURCE_URLS, group_into_posts
from creeparr.providers.onlyfans.client import OnlyFansClient
from creeparr.providers.reddit.client import _post_from_json
from creeparr.providers.youtube import YouTubeProvider
from creeparr.services import build_services
from tests import onlyfans_fixtures as of
from tests import patreon_fixtures as fx
from tests.test_instagram import _items


@pytest.fixture
def providers(env):
    return list(build_services(env).providers)


def test_names_are_unique_and_match_the_settings_groups(providers):
    names = [p.name for p in providers]
    assert len(set(names)) == len(names)
    for name in names:
        assert name in AppSettings.model_fields, f"{name} has no settings group"


def test_account_fields_exist_and_cover_the_secrets(providers):
    secrets = secret_fields()
    for provider in providers:
        group = AppSettings.model_fields[provider.name].annotation.model_fields  # type: ignore[union-attr]
        keys = set(provider.description.credential_keys)
        assert keys <= set(group), f"{provider.name}: {keys - set(group)} aren't settings"
        # Every secret is set through the account form; not every account field is secret.
        assert set(secrets.get(provider.name, {})) <= keys, provider.name


def _patreon_types() -> set[str]:
    posts = [
        fx.native_video_post("p1"),
        fx.youtube_post("p2"),
        fx.vimeo_post("p3"),
        fx.image_post("p4", ["m1"]),
        fx.post_resource("p5", post_type="text_only", post_file=None),
    ]
    return {post_from_resource(p, IncludedIndex(fx.posts_page([p]))).post_type for p in posts}


def _youtube_types(env, settings, session_factory, bus) -> set[str]:
    prov = YouTubeProvider(env, settings, session_factory, bus)
    return {prov._post_from_entry({"id": "v1", "title": "t"}, "UC1").post_type}


@pytest.mark.parametrize("name", ["patreon", "onlyfans", "youtube", "instagram", "reddit"])
def test_every_post_type_a_parser_stores_is_declared(
    providers, env, settings, session_factory, bus, name
):
    produced = {
        "patreon": _patreon_types,
        "onlyfans": lambda: {
            OnlyFansClient._post_from_json(of.post(1)).post_type,
            OnlyFansClient._message_from_json(of.message(2), of.CREATOR_ID).post_type,
        },
        "youtube": lambda: _youtube_types(env, settings, session_factory, bus),
        "instagram": lambda: {
            post.post_type
            for source in SOURCE_URLS
            for post in group_into_posts(_items(), source, "someone")
        },
        "reddit": lambda: {
            _post_from_json(
                {"id": "a", "title": "t", "created_utc": 1, "url": "x"}, "r/a"
            ).post_type
        },
    }[name]()
    provider = next(p for p in providers if p.name == name)
    declared = set(provider.description.post_types)
    assert produced and produced <= declared, f"{name}: {produced - declared} not declared"


def test_the_secrets_are_the_ones_marked():
    secrets = {group: set(marks) for group, marks in secret_fields().items()}
    assert secrets == {
        "patreon": {"session_id", "cookies_txt"},
        "onlyfans": {"sess", "auth_id", "x_bc", "cookies_txt"},
        "youtube": {"cookies_txt"},
        "instagram": {"sessionid", "cookies_txt"},
        "reddit": {"client_secret"},
        "security": {"password_hash"},
    }


def test_a_text_secret_reads_back_as_whether_it_is_set(settings):
    settings.update(
        {"youtube": {"cookies_txt": "# Netscape\n.youtube.com\tTRUE"}}, allow_secrets=True
    )
    masked = settings.masked()["youtube"]
    assert masked["cookies_txt"] == "" and masked["has_cookies_txt"] is True


def test_scanning_a_creator_that_is_gone_is_refused(env, engine):
    from creeparr.core.errors import NotFound

    with pytest.raises(NotFound):
        build_services(env).scan_manager.request_scan(9999)

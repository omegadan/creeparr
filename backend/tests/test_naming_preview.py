"""The naming preview shows what the path rules make of sample posts, and a template the
rules can't render is never saved."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from creeparr.app import create_app
from creeparr.core.errors import ValidationFailed
from creeparr.core.settings_service import NamingSettings
from creeparr.db.engine import session_scope
from creeparr.db.models import History, Setting
from creeparr.downloader.paths import SAMPLES, TOKENS, Sample, file_name, post_folder, preview
from tests.test_api import UI_HEADERS

PREVIEW = "/api/v1/settings/naming-preview"
FOLDER = "Example Creator/2026-03-14 - Episode 12_ Behind the scenes _ Q&A [98765432]"


@pytest.fixture
def api(env):
    with TestClient(create_app(env, start_background=False), headers=UI_HEADERS) as c:
        yield c


@pytest.mark.parametrize(
    "edited",
    [
        {},
        {"max_component_length": 20},
        {
            "post_folder_template": "{post_type}/{title}",
            "file_template": "{title} {media_index}.{ext}",
        },
    ],
)
def test_the_preview_is_where_a_download_would_put_each_sample(api, edited):
    rows = api.post(PREVIEW, json=edited).json()["samples"]
    naming = NamingSettings(**edited)
    assert [r["full_path"] for r in rows] == [
        f"{post_folder(naming, s)}/{file_name(naming, s, s.ext)}" for s in SAMPLES
    ]


def test_the_default_preview():
    rows = [p.full_path for p in preview(NamingSettings())]
    assert rows == [
        f"{FOLDER}/episode-12.mp4",
        f"{FOLDER}/Episode 12_ Behind the scenes _ Q&A - image 2.jpg",
        f"{FOLDER}/Episode 12_ Behind the scenes _ Q&A.mp4",
        # No publish date: the date it was first seen.
        "Example Creator/2026-03-15 - Bonus clip [98765433]/bonus.mp4",
    ]


def test_the_preview_shows_the_settings_being_edited_not_the_saved_ones(api):
    rows = api.post(
        PREVIEW,
        json={
            "post_folder_template": "{creator_vanity}/{title}",
            "file_template": "{media_index} {filename}",
            "max_component_length": 20,
        },
    ).json()["samples"]
    assert rows[0]["post_folder"] == "examplecreator/Episode 12_ Behind t"  # 20 bytes
    assert rows[0]["file"] == "1 episode-12.mp4"


def test_a_template_that_cant_render_is_refused_with_the_reason(api):
    r = api.post(PREVIEW, json={"post_folder_template": "{creator}/{title"})
    assert r.status_code == 422
    assert r.json()["error"]["detail"] == (
        "post_folder_template: Expected '}' before end of string"
    )
    r = api.put("/api/v1/settings", json={"naming": {"post_folder_template": "{0}/{title}"}})
    assert r.status_code == 422
    assert "Use a token's name in braces" in r.json()["error"]["detail"]
    assert api.get("/api/v1/settings").json()["naming"]["post_folder_template"] == (
        NamingSettings().post_folder_template
    )


@pytest.mark.parametrize("template", ["{title", "a}", "{0}", "{title.foo}", "{creator}/{filename}"])
def test_a_file_name_template_that_cant_be_used_isnt_saved(settings, template):
    with pytest.raises(ValidationFailed):
        settings.update({"naming": {"file_template": template}})


def test_the_file_name_template_cant_create_folders(api):
    r = api.post(PREVIEW, json={"file_template": "{post_type}/{filename}"})
    assert r.status_code == 422
    assert "can't create folders" in r.json()["error"]["detail"]


def test_unknown_tokens_are_reported_but_still_render(api):
    body = api.post(
        PREVIEW,
        json={"post_folder_template": "{creator}/{titel}/{filename}", "file_template": "{ext}"},
    ).json()
    # {filename} names a file, so it's no token in a folder.
    assert body["unknown"] == ["filename", "titel"]
    assert body["samples"][0]["full_path"] == "Example Creator/mp4"


def test_the_tokens_are_the_path_rules_own(api):
    tokens = api.post(PREVIEW, json={}).json()["tokens"]
    assert [t["name"] for t in tokens] == [t.name for t in TOKENS]
    examples = {t["name"]: t["example"] for t in tokens}
    assert examples["published"] == "2026-03-14"
    assert examples["embed_provider"] == "YouTube"  # from the sample that has one
    assert examples["filename"] == "episode-12.mp4"


def test_a_stored_setting_that_cant_be_used_is_reset_alone(settings, session_factory):
    with session_scope(session_factory) as s:
        s.add(
            Setting(
                key="naming",
                value={
                    "post_folder_template": "{creator}/{title}",
                    "file_template": "{creator}/{filename}",
                    "write_sidecars": False,
                },
            )
        )
    naming = settings.reload().naming
    assert naming.file_template == "{filename}"
    # The rest of the group is kept, not reset with it.
    assert naming.post_folder_template == "{creator}/{title}"
    assert naming.write_sidecars is False
    with session_scope(session_factory) as s:
        assert s.get(Setting, "naming").value["file_template"] == "{filename}"
        warnings = s.execute(select(History.level, History.message)).all()
    assert warnings == [
        ("warning", "naming.file_template couldn't be used and was reset to the default")
    ]
    settings.reload()  # stored repaired: no second warning
    with session_scope(session_factory) as s:
        assert len(s.execute(select(History)).all()) == 1


@pytest.mark.parametrize(
    ("sample", "name"),
    [
        (Sample("a video's own file", "mp4", remote_file_name="clip.MOV"), "clip.mp4"),
        (
            Sample("the post's only video", "mp4", source="native_hls"),
            "Episode 12_ Behind the scenes _ Q&A.mp4",
        ),
        (
            Sample("a second video", "mp4", order_index=2),
            "Episode 12_ Behind the scenes _ Q&A - video 2.mp4",
        ),
        (
            Sample("a first image", "jpg", kind="image"),
            "Episode 12_ Behind the scenes _ Q&A - image 1.jpg",
        ),
        (Sample("an untitled post", "mp3", kind="audio", post_title=""), "untitled - audio 1.mp3"),
    ],
)
def test_a_file_without_its_own_name_is_named_after_the_post(sample, name):
    assert file_name(NamingSettings(), sample, sample.ext) == name


def test_a_stored_group_that_isnt_settings_at_all_is_reset(settings, session_factory):
    with session_scope(session_factory) as s:
        s.add(Setting(key="history", value=["not", "a", "group"]))
    assert settings.reload().history.retention_days == 90

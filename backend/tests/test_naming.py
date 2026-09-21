from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from creeparr.core.naming import render_template, sanitize_component, split_name, unique_path


def test_sanitize_illegal_and_whitespace():
    assert sanitize_component('  a/b\\c:d*e?f"g<h>i|j  ') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_component("multi   space\tand\nnewline") == "multi space and newline"


def test_sanitize_trailing_dots_and_reserved():
    assert sanitize_component("name...") == "name"
    assert sanitize_component("CON") == "CON_"
    assert sanitize_component("con.txt") == "con.txt_"
    assert sanitize_component("") == "untitled"
    assert sanitize_component("   ") == "untitled"


def test_sanitize_truncates_on_char_boundary():
    s = sanitize_component("é" * 200, max_len=15)
    assert len(s.encode()) <= 15
    assert s == "é" * 7


def test_render_template_with_dates_and_slashes():
    values = {
        "creator": "Some / Creator",
        "title": "Ep 1: The <Start>",
        "post_id": "42",
        "published": datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
    }
    out = render_template("{creator}/{published:%Y-%m-%d} - {title} [{post_id}]", values)
    assert out.parts == ("Some _ Creator", "2026-01-02 - Ep 1_ The _Start_ [42]")


def test_render_template_missing_token_and_dotdot():
    out = render_template("../{creator}/{missing}/x", {"creator": "c"})
    assert out.parts == ("c", "x")


def test_split_name_and_unique_path(tmp_path: Path):
    assert split_name("video.MP4") == ("video", "mp4")
    assert split_name(None) == ("", "")
    p = tmp_path / "a.mp4"
    assert unique_path(p) == p
    p.write_text("x")
    assert unique_path(p) == tmp_path / "a (2).mp4"
    (tmp_path / "a (2).mp4").write_text("x")
    assert unique_path(p) == tmp_path / "a (3).mp4"


def test_legacy_database_is_adopted(tmp_path: Path):
    from creeparr.config import EnvConfig

    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "patreonarr.db").write_bytes(b"old")
    (cfg / "patreonarr.db-wal").write_bytes(b"wal")
    env = EnvConfig(config_dir=cfg, download_dir=tmp_path / "dl")
    env.ensure_dirs()
    assert (cfg / "creeparr.db").read_bytes() == b"old"
    assert (cfg / "creeparr.db-wal").exists()
    assert not (cfg / "patreonarr.db").exists()


def test_download_root_per_provider(tmp_path: Path):
    from creeparr.config import EnvConfig

    base = tmp_path / "dl"
    of = tmp_path / "dl-of"
    env = EnvConfig(config_dir=tmp_path / "c", download_dir=base, onlyfans_download_dir=of)
    assert env.download_root("patreon") == base
    assert env.download_root("onlyfans") == of
    assert set(env.download_roots()) == {"downloads", "onlyfans"}
    # falls back to the main root when unset
    env2 = EnvConfig(config_dir=tmp_path / "c", download_dir=base)
    assert env2.download_root("onlyfans") == base
    assert set(env2.download_roots()) == {"downloads"}


def test_html_to_text():
    from creeparr.downloader.metadata import html_to_text

    assert html_to_text("<p>Hello <b>world</b> &amp; more</p>") == "Hello world & more"
    assert html_to_text(None) == ""
    assert len(html_to_text("<p>" + "x" * 5000 + "</p>", limit=100)) == 100

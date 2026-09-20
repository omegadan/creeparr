from __future__ import annotations

from patreonarr.db.enums import MediaKind, MediaSource
from patreonarr.patreon.media_resolver import CreatorPrefs, is_hls_url, resolve_media
from patreonarr.patreon.parsing import IncludedIndex, post_from_resource
from tests import patreon_fixtures as fx


def _post(resource, included=None):
    page = fx.posts_page([resource], included=included)
    return post_from_resource(resource, IncludedIndex(page))


def test_native_direct_video():
    specs = resolve_media(_post(fx.native_video_post("p1")))
    assert len(specs) == 1
    s = specs[0]
    assert s.kind == MediaKind.VIDEO and s.source == MediaSource.NATIVE_DIRECT
    assert s.media_key == "postfile:p1" and s.file_name == "episode.mp4"


def test_native_hls_video():
    specs = resolve_media(_post(fx.native_video_post("p2", hls=True)))
    assert specs[0].source == MediaSource.NATIVE_HLS
    assert is_hls_url(specs[0].url)


def test_embeds():
    yt = resolve_media(_post(fx.youtube_post("p3")))[0]
    assert yt.source == MediaSource.EMBED_YOUTUBE and yt.media_key == "embed:p3"
    vm = resolve_media(_post(fx.vimeo_post("p4")))[0]
    assert vm.source == MediaSource.EMBED_VIMEO
    other = fx.post_resource(
        "p5", post_type="video_embed", embed={"provider": "Wistia", "url": "https://wistia.com/x"}
    )
    assert resolve_media(_post(other))[0].source == MediaSource.EMBED_OTHER


def test_image_post_ordered_and_deduped():
    included = [
        fx.media_resource("m1"),
        fx.media_resource("m2", file_name="two.png", mimetype="image/png"),
    ]
    specs = resolve_media(
        _post(fx.image_post("p6", ["m1", "m2"], image_order=["m2", "m1"]), included)
    )
    assert [s.media_key for s in specs] == ["media:m2", "media:m1"]
    assert [s.order_index for s in specs] == [1, 2]
    assert all(s.kind == MediaKind.IMAGE for s in specs)


def test_audio_and_attachments():
    included = [
        fx.media_resource("a1", file_name="track.mp3", mimetype="audio/mpeg"),
        fx.media_resource("f1", file_name="notes.pdf", mimetype="application/pdf"),
        fx.media_resource("f2", file_name="bonus.mp4", mimetype="video/mp4"),
    ]
    res = fx.post_resource(
        "p7",
        post_type="audio_file",
        post_file={
            "name": "track.mp3",
            "url": f"{fx.CDN}/a1/track.mp3?token=x",
            "mimetype": "audio/mpeg",
        },
        audio=["a1"],
        attachments=["f1", "f2"],
    )
    specs = resolve_media(_post(res, included))
    kinds = {s.media_key: s.kind for s in specs}
    # post_file and the audio media share the same URL -> deduped to the post_file spec
    assert kinds == {
        "postfile:p7": MediaKind.AUDIO,
        "attachment:f1": MediaKind.ATTACHMENT,
        "attachment:f2": MediaKind.VIDEO,
    }


def test_no_access_yields_nothing():
    assert resolve_media(_post(fx.native_video_post("p8", can_view=False))) == []


def test_prefs():
    p = CreatorPrefs()
    assert p.wants(MediaKind.VIDEO) and not p.wants(MediaKind.IMAGE)
    assert CreatorPrefs(include_attachments=True).wants(MediaKind.ATTACHMENT)

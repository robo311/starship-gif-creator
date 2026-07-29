"""URL handling and format selection — all offline."""

from server import youtube

TARGET = "https://www.youtube.com/watch?v=zhHB4dZTChw"


def test_parses_the_standard_watch_url():
    assert youtube.parse_video_id(TARGET) == "zhHB4dZTChw"


def test_parses_the_other_youtube_url_shapes():
    cases = {
        "https://youtu.be/zhHB4dZTChw": "zhHB4dZTChw",
        "https://www.youtube.com/shorts/zhHB4dZTChw": "zhHB4dZTChw",
        "https://www.youtube.com/embed/zhHB4dZTChw": "zhHB4dZTChw",
        "https://www.youtube.com/live/zhHB4dZTChw": "zhHB4dZTChw",
        "https://www.youtube.com/watch?v=zhHB4dZTChw&t=42s": "zhHB4dZTChw",
        "https://m.youtube.com/watch?app=desktop&v=zhHB4dZTChw": "zhHB4dZTChw",
        "zhHB4dZTChw": "zhHB4dZTChw",
    }
    for url, expected in cases.items():
        assert youtube.parse_video_id(url) == expected, url


def test_rejects_things_that_are_not_video_urls():
    for url in ("", "https://example.com/video", "not a url", "https://youtube.com/"):
        assert youtube.parse_video_id(url) is None


def test_format_selector_prefers_h264_for_browser_playback():
    selector = youtube.format_selector(720)
    assert selector.index("avc1") < len(selector)
    assert selector.startswith("bv*[vcodec^=avc1]")
    assert "height<=?720" in selector


def test_format_selector_always_has_a_final_fallback():
    assert youtube.format_selector(1080).endswith("/b")


def test_cached_video_finds_a_playable_file(cache_dir, synthetic_video):
    from .conftest import SYNTHETIC_ID

    found = youtube.cached_video(cache_dir / "videos", SYNTHETIC_ID)
    assert found == synthetic_video


def test_cached_video_returns_none_when_absent(cache_dir):
    assert youtube.cached_video(cache_dir / "videos", "doesnotexist") is None


def test_percent_parsing_survives_yt_dlp_oddities():
    assert youtube._parse_percent(" 42.5%") == 0.425
    assert youtube._parse_percent("100.0%") == 1.0
    assert youtube._parse_percent("NA") == 0.0
    assert youtube._parse_percent("") == 0.0

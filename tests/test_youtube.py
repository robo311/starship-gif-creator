"""Provider URL handling and format selection — all offline."""

import pytest

from server import sources, youtube

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


def test_parses_twitter_and_x_status_urls_to_the_same_cache_id():
    cases = (
        "https://twitter.com/NASA/status/1681100325046751233",
        "https://x.com/NASA/status/1681100325046751233?s=20",
        "https://mobile.twitter.com/NASA/status/1681100325046751233/video/1",
        "https://x.com/i/web/status/1681100325046751233",
    )
    for url in cases:
        assert sources.parse_source_id(url) == "twitter_1681100325046751233"


def test_parses_instagram_reel_urls():
    cases = (
        "https://www.instagram.com/reel/C9abc_DEF12/",
        "https://instagram.com/reel/C9abc_DEF12/?igsh=example",
    )
    for url in cases:
        assert sources.parse_source_id(url) == "instagram_C9abc_DEF12"


def test_parses_canonical_tiktok_video_urls():
    cases = (
        "https://www.tiktok.com/@creator/video/7380000000000000001",
        "https://m.tiktok.com/@creator/video/7380000000000000001?lang=en",
    )
    for url in cases:
        assert sources.parse_source_id(url) == "tiktok_7380000000000000001"


def test_short_tiktok_links_fall_through_to_metadata_resolution():
    assert sources.parse_source_id("https://vm.tiktok.com/ZMexample/") is None


def test_provider_prefixes_prevent_cross_site_id_collisions():
    twitter = {"id": "12345", "extractor_key": "Twitter"}
    instagram = {"id": "12345", "extractor_key": "Instagram"}
    assert sources.source_id("https://x.com/user/status/12345", twitter) == "twitter_12345"
    assert sources.source_id("https://instagram.com/p/example", instagram) == "instagram_12345"


def test_instagram_and_tiktok_keep_the_url_derived_cache_id():
    instagram_url = "https://instagram.com/reel/C9abc_DEF12/"
    tiktok_url = "https://www.tiktok.com/@creator/video/7380000000000000001"
    assert sources.source_id(
        instagram_url, {"id": "C9abc_DEF12", "extractor_key": "Instagram"}
    ) == "instagram_C9abc_DEF12"
    assert sources.source_id(
        tiktok_url, {"id": "7380000000000000001", "extractor_key": "TikTok"}
    ) == "tiktok_7380000000000000001"


def test_browser_cookies_are_only_used_when_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("STARSHIP_COOKIES_FROM_BROWSER", raising=False)
    assert sources._browser_cookie_args() == []
    monkeypatch.setenv("STARSHIP_COOKIES_FROM_BROWSER", "chrome:Default")
    assert sources._browser_cookie_args() == ["--cookies-from-browser", "chrome:Default"]


def test_metadata_request_passes_the_opted_in_browser_session(monkeypatch):
    captured = {}

    def fake_run(argv, timeout):
        captured["argv"] = argv
        return '{"id":"C9abc_DEF12","extractor_key":"Instagram"}'

    monkeypatch.setenv("STARSHIP_COOKIES_FROM_BROWSER", "safari")
    monkeypatch.setattr(sources.media, "find_binary", lambda name: name)
    monkeypatch.setattr(sources.media, "run", fake_run)
    sources.fetch_metadata("https://instagram.com/reel/C9abc_DEF12/")
    assert captured["argv"][-3:] == ["--cookies-from-browser", "safari",
                                     "https://instagram.com/reel/C9abc_DEF12/"]


def test_private_provider_errors_are_clear_and_explain_the_cookie_option(monkeypatch):
    monkeypatch.delenv("STARSHIP_COOKIES_FROM_BROWSER", raising=False)
    message = sources._friendly_access_error("https://instagram.com/reel/private/")
    assert "private" in message
    assert "STARSHIP_COOKIES_FROM_BROWSER" in message
    assert "chrome" in message


def test_instagram_image_posts_get_one_plain_error(monkeypatch):
    repeated = "\n".join(
        f"ERROR: [Instagram] image{i}: No video formats found!" for i in range(8)
    )

    def fail(argv, timeout):
        raise sources.media.CommandFailed(argv, 1, repeated)

    monkeypatch.delenv("STARSHIP_COOKIES_FROM_BROWSER", raising=False)
    monkeypatch.setattr(sources.media, "find_binary", lambda name: name)
    monkeypatch.setattr(sources.media, "run", fail)

    with pytest.raises(sources.DownloadError) as caught:
        sources.fetch_metadata("https://www.instagram.com/p/DbXMmExample/")

    message = str(caught.value)
    assert message == (
        "That Instagram post does not contain a downloadable video. "
        "Paste a Reel or a post that includes a video."
    )
    assert "yt-dlp" not in message
    assert "image7" not in message


def test_any_leaked_instagram_extractor_dump_is_normalized():
    raw = (
        "yt-dlp exited 1: ERROR: [Instagram] first: No video formats found!\n"
        "ERROR: [Instagram] second: No video formats found!"
    )
    assert sources.public_error_message(raw) == (
        "That Instagram post does not contain a downloadable video. "
        "Paste a Reel or a post that includes a video."
    )


def test_youtube_ids_remain_compatible_with_existing_cache_names():
    info = {"id": "zhHB4dZTChw", "extractor_key": "Youtube"}
    assert sources.source_id(TARGET, info) == "zhHB4dZTChw"


def test_arbitrary_provider_ids_are_made_filesystem_safe_and_bounded():
    info = {"id": "unsafe/id:" + ("x" * 100), "extractor_key": "Some Site"}
    result = sources.source_id("https://videos.example/watch/1", info)
    assert len(result) <= sources.SOURCE_ID_MAX
    assert result.startswith("somesite_")
    assert result.replace("_", "").isalnum()


def test_only_web_urls_or_bare_youtube_ids_are_accepted():
    sources.validate_url(TARGET)
    sources.validate_url("https://x.com/user/status/12345")
    sources.validate_url("zhHB4dZTChw")
    for value in ("not a url", "file:///tmp/video.mp4", "ftp://example.com/video"):
        with pytest.raises(sources.DownloadError):
            sources.validate_url(value)


def test_rejects_things_that_are_not_video_urls():
    for url in (
        "",
        "https://example.com/video",
        "https://example.com/watch?v=zhHB4dZTChw",
        "not a url",
        "https://youtube.com/",
    ):
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

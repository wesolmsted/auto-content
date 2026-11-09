import pytest

from engine.db.enums import Platform
from engine.handoff.links import PostLink, PostUrlError, parse_post_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.instagram.com/reel/DAbc_12-xY/",
            PostLink("DAbc_12-xY", "https://www.instagram.com/reel/DAbc_12-xY/"),
        ),
        (
            "https://instagram.com/reel/DAbc12xY?igsh=MTc4bWx6",
            PostLink("DAbc12xY", "https://www.instagram.com/reel/DAbc12xY/"),
        ),
        (
            "  https://www.instagram.com/reels/DAbc12xY  ",
            PostLink("DAbc12xY", "https://www.instagram.com/reel/DAbc12xY/"),
        ),
        (
            "https://www.instagram.com/some.creator/reel/DAbc12xY/",
            PostLink("DAbc12xY", "https://www.instagram.com/reel/DAbc12xY/"),
        ),
    ],
)
def test_parses_instagram_reel_urls_to_the_shortcode(url: str, expected: PostLink) -> None:
    assert parse_post_url(Platform.IG_TRIAL, url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.tiktok.com/@some.creator/video/7301234567890123456",
            PostLink(
                "7301234567890123456",
                "https://www.tiktok.com/@some.creator/video/7301234567890123456",
            ),
        ),
        (
            "https://m.tiktok.com/@creator/video/7301234567890123456/?is_from_webapp=1&lang=en",
            PostLink(
                "7301234567890123456", "https://www.tiktok.com/@creator/video/7301234567890123456"
            ),
        ),
        (
            "https://tiktok.com/video/7301234567890123456",
            PostLink("7301234567890123456", "https://www.tiktok.com/video/7301234567890123456"),
        ),
    ],
)
def test_parses_tiktok_video_urls_to_the_video_id(url: str, expected: PostLink) -> None:
    assert parse_post_url(Platform.TIKTOK, url) == expected


@pytest.mark.parametrize(
    ("platform", "url", "message"),
    [
        (Platform.IG_TRIAL, "", "paste"),
        (Platform.IG_TRIAL, "not a url", "instagram.com"),
        (Platform.IG_TRIAL, "https://www.instagram.com/p/DAbc12xY/", "/reel/"),
        (Platform.IG_TRIAL, "https://www.instagram.com/reel/", "/reel/"),
        (Platform.IG_TRIAL, "https://evil.example/reel/DAbc12xY/", "instagram.com"),
        (Platform.IG_TRIAL, "ftp://www.instagram.com/reel/DAbc12xY/", "instagram.com"),
        (Platform.IG_TRIAL, "https://www.tiktok.com/@a/video/7301234567890123456", "instagram"),
        (Platform.TIKTOK, "https://www.instagram.com/reel/DAbc12xY/", "tiktok.com"),
        (Platform.TIKTOK, "https://vm.tiktok.com/ZMabc123/", "full"),
        (Platform.TIKTOK, "https://www.tiktok.com/@creator/video/abc", "/video/"),
        (Platform.TIKTOK, "https://www.tiktok.com/@creator", "/video/"),
        (Platform.TIKTOK, "https://nottiktok.com/@a/video/7301234567890123456", "tiktok.com"),
    ],
)
def test_rejects_urls_it_cannot_parse_with_a_clear_message(
    platform: Platform, url: str, message: str
) -> None:
    with pytest.raises(PostUrlError, match=message):
        parse_post_url(platform, url)

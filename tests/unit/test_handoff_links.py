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
    ("platform", "url", "message"),
    [
        (Platform.IG_TRIAL, "", "paste"),
        (Platform.IG_TRIAL, "not a url", "instagram.com"),
        (Platform.IG_TRIAL, "https://www.instagram.com/p/DAbc12xY/", "/reel/"),
        (Platform.IG_TRIAL, "https://www.instagram.com/reel/", "/reel/"),
        (Platform.IG_TRIAL, "https://evil.example/reel/DAbc12xY/", "instagram.com"),
        (Platform.IG_TRIAL, "ftp://www.instagram.com/reel/DAbc12xY/", "instagram.com"),
    ],
)
def test_rejects_urls_it_cannot_parse_with_a_clear_message(
    platform: Platform, url: str, message: str
) -> None:
    with pytest.raises(PostUrlError, match=message):
        parse_post_url(platform, url)

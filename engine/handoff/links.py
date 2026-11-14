"""Parse a pasted post URL into the id and permalink stored on a manually linked post (§7.6)."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from engine.db.enums import Platform

INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}

INSTAGRAM_REEL_PATH = re.compile(r"^/(?:[^/]+/)?reels?/(?P<code>[A-Za-z0-9_-]+)/?$")

INSTAGRAM_EXAMPLE = "https://www.instagram.com/reel/<code>/"


class PostUrlError(ValueError):
    pass


@dataclass(frozen=True)
class PostLink:
    external_id: str
    permalink: str


def parse_post_url(platform: Platform, url: str) -> PostLink:
    """The post's id and canonical permalink, or `PostUrlError` saying what URL was expected.

    Instagram ids are the reel shortcode.
    """
    url = url.strip()
    if not url:
        raise PostUrlError("paste the post's URL")
    parts = urlsplit(url)
    host = parts.hostname or ""
    return _parse_instagram(parts.scheme, host, parts.path)


def _parse_instagram(scheme: str, host: str, path: str) -> PostLink:
    if scheme not in {"http", "https"} or host not in INSTAGRAM_HOSTS:
        raise PostUrlError(
            f"not an Instagram link: expected an instagram.com URL like {INSTAGRAM_EXAMPLE}"
        )
    match = INSTAGRAM_REEL_PATH.match(path)
    if match is None:
        raise PostUrlError(f"not a reel link: expected a URL like {INSTAGRAM_EXAMPLE}")
    code = match["code"]
    return PostLink(external_id=code, permalink=f"https://www.instagram.com/reel/{code}/")

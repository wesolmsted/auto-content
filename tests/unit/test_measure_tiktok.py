"""TikTokReader against recorded-style Display API responses (no network)."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from engine.config import load_settings
from engine.db.enums import Platform
from engine.measure.api import PlatformReadError
from engine.measure.models import MetricSnapshot, PlatformVideo
from engine.measure.tiktok import TikTokReader

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
HTTP = load_settings(REPO_CONFIG_DIR, env_file=None).http
API = "https://open.example.test/v2"
TOKEN = "act.tiktok-secret"
LIST_URL = f"{API}/video/list/"
QUERY_URL = f"{API}/video/query/"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SINCE = datetime(2026, 11, 7, 0, 0, tzinfo=UTC)
OK = {"code": "ok", "message": "", "log_id": "20261109120000ABC"}


def tiktok_video(video_id: str, created: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "id": video_id,
        "create_time": int(created.timestamp()),
        "video_description": "Night Drive #fyp",
        "duration": 7,
        "share_url": f"https://www.tiktok.com/@owner/video/{video_id}",
        "view_count": 100,
        **extra,
    }


PAGE_1 = {
    "data": {
        "videos": [
            tiktok_video("7303", datetime(2026, 11, 9, 1, 30, tzinfo=UTC)),
            tiktok_video("7302", datetime(2026, 11, 8, 17, 0, tzinfo=UTC), duration=12),
        ],
        "cursor": 1731085200000,
        "has_more": True,
    },
    "error": OK,
}
PAGE_2 = {
    "data": {
        "videos": [
            {"id": "7301", "create_time": int(SINCE.timestamp())},
            tiktok_video("7300", datetime(2026, 11, 6, 23, 0, tzinfo=UTC)),
        ],
        "cursor": 1730930000000,
        "has_more": True,
    },
    "error": OK,
}


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with httpx.Client() as client:
        yield client


def make_reader(client: httpx.Client, sleeps: list[float] | None = None) -> TikTokReader:
    return TikTokReader(
        client,
        API + "/",
        TOKEN,
        HTTP,
        sleep=(sleeps if sleeps is not None else []).append,
        clock=lambda: NOW,
    )


def list_pages(request: httpx.Request) -> httpx.Response:
    cursor = json.loads(request.content).get("cursor")
    pages = {None: PAGE_1, 1731085200000: PAGE_2}
    if cursor not in pages:
        return httpx.Response(500, json={"error": {"message": "page should not be requested"}})
    return httpx.Response(200, json=pages[cursor])


def test_platform_is_tiktok(client: httpx.Client) -> None:
    assert make_reader(client).platform == Platform.TIKTOK


@respx.mock
def test_recent_videos_pages_by_cursor_until_videos_are_older_than_since(
    client: httpx.Client,
) -> None:
    route = respx.post(LIST_URL).mock(side_effect=list_pages)

    videos = make_reader(client).recent_videos(SINCE)

    assert videos == [
        PlatformVideo(
            platform=Platform.TIKTOK,
            external_id="7303",
            permalink="https://www.tiktok.com/@owner/video/7303",
            posted_at=datetime(2026, 11, 9, 1, 30, tzinfo=UTC),
            caption="Night Drive #fyp",
            duration_s=7.0,
        ),
        PlatformVideo(
            platform=Platform.TIKTOK,
            external_id="7302",
            permalink="https://www.tiktok.com/@owner/video/7302",
            posted_at=datetime(2026, 11, 8, 17, 0, tzinfo=UTC),
            caption="Night Drive #fyp",
            duration_s=12.0,
        ),
        PlatformVideo(
            platform=Platform.TIKTOK,
            external_id="7301",
            permalink=None,
            posted_at=SINCE,
            caption="",
            duration_s=None,
        ),
    ]
    assert route.call_count == 2
    first = route.calls[0].request
    assert first.url.params["fields"] == (
        "id,create_time,video_description,duration,share_url,"
        "view_count,like_count,comment_count,share_count"
    )
    assert first.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(first.content) == {"max_count": 20}
    assert json.loads(route.calls[1].request.content) == {"max_count": 20, "cursor": 1731085200000}


@respx.mock
def test_recent_videos_stops_when_has_more_is_false(client: httpx.Client) -> None:
    last = {"data": {"videos": [], "cursor": 0, "has_more": False}, "error": OK}
    route = respx.post(LIST_URL).mock(return_value=httpx.Response(200, json=last))
    assert make_reader(client).recent_videos(SINCE) == []
    assert route.call_count == 1


@respx.mock
def test_recent_videos_retries_a_429_with_backoff(client: httpx.Client) -> None:
    last = {"data": {"videos": [], "has_more": False}, "error": OK}
    respx.post(LIST_URL).mock(
        side_effect=[
            httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message": "slow"}}),
            httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message": "slow"}}),
            httpx.Response(200, json=last),
        ]
    )
    sleeps: list[float] = []
    assert make_reader(client, sleeps).recent_videos(SINCE) == []
    assert sleeps == [HTTP.backoff_base_s, HTTP.backoff_base_s * 2]


@respx.mock
def test_an_error_code_in_a_200_body_raises_platform_read_error(client: httpx.Client) -> None:
    body = {
        "data": {},
        "error": {"code": "scope_not_authorized", "message": "video.list not granted"},
    }
    respx.post(LIST_URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(PlatformReadError, match=r"scope_not_authorized: video\.list not granted"):
        make_reader(client).recent_videos(SINCE)


@respx.mock
def test_an_http_error_raises_platform_read_error_without_the_token(client: httpx.Client) -> None:
    body = {"error": {"code": "access_token_invalid", "message": "The access token is invalid"}}
    respx.post(LIST_URL).mock(return_value=httpx.Response(401, json=body))
    with pytest.raises(PlatformReadError, match="HTTP 401: The access token is invalid") as info:
        make_reader(client).recent_videos(SINCE)
    assert TOKEN not in str(info.value)


@respx.mock
def test_fetch_metrics_queries_one_video_and_maps_the_counts(client: httpx.Client) -> None:
    body = {
        "data": {
            "videos": [
                {
                    "id": "7303",
                    "view_count": 2400,
                    "like_count": 310,
                    "comment_count": 12,
                    "share_count": 25,
                }
            ]
        },
        "error": OK,
    }
    route = respx.post(QUERY_URL).mock(return_value=httpx.Response(200, json=body))

    snapshot = make_reader(client).fetch_metrics("7303")

    assert snapshot == MetricSnapshot(
        fetched_at=NOW, views=2400, likes=310, comments=12, shares=25, raw=body
    )
    request = route.calls.last.request
    assert request.url.params["fields"] == "id,view_count,like_count,comment_count,share_count"
    assert json.loads(request.content) == {"filters": {"video_ids": ["7303"]}}
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"


@respx.mock
def test_fetch_metrics_leaves_missing_counts_as_none(client: httpx.Client) -> None:
    body = {"data": {"videos": [{"id": "7303", "view_count": 50}]}, "error": OK}
    respx.post(QUERY_URL).mock(return_value=httpx.Response(200, json=body))
    snapshot = make_reader(client).fetch_metrics("7303")
    assert (snapshot.views, snapshot.likes, snapshot.comments, snapshot.shares) == (
        50,
        None,
        None,
        None,
    )


@respx.mock
def test_fetch_metrics_for_an_unknown_video_raises(client: httpx.Client) -> None:
    body = {"data": {"videos": []}, "error": OK}
    respx.post(QUERY_URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(PlatformReadError, match="7399"):
        make_reader(client).fetch_metrics("7399")

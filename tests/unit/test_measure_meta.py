"""MetaReader against recorded-style Graph API responses (no network)."""

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
from engine.measure.meta import MetaReader
from engine.measure.models import MetricSnapshot, PlatformVideo

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
HTTP = load_settings(REPO_CONFIG_DIR, env_file=None).http
GRAPH = "https://graph.example.test/v23.0"
IG_USER = "17841400000000000"
TOKEN = "EAAG-long-lived-secret"
MEDIA_URL = f"{GRAPH}/{IG_USER}/media"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SINCE = datetime(2026, 11, 7, 0, 0, tzinfo=UTC)


def media(
    media_id: str,
    timestamp: str,
    product: str = "REELS",
    media_type: str = "VIDEO",
    caption: str | None = "Night Drive #fyp",
) -> dict[str, Any]:
    item = {
        "id": media_id,
        "permalink": f"https://www.instagram.com/reel/code{media_id}/",
        "timestamp": timestamp,
        "media_type": media_type,
        "media_product_type": product,
    }
    if caption is not None:
        item["caption"] = caption
    return item


PAGE_1 = {
    "data": [
        media("103", "2026-11-09T01:30:00+0000"),
        media("102", "2026-11-08T22:00:00+0000", product="FEED", media_type="IMAGE"),
        media("101", "2026-11-08T17:05:00+0000", caption=None),
    ],
    "paging": {
        "cursors": {"after": "AFTER1"},
        "next": f"{MEDIA_URL}?fields=id&limit=50&after=AFTER1&access_token={TOKEN}",
    },
}
PAGE_2 = {
    "data": [
        media("100", "2026-11-07T00:00:00+0000"),
        media("099", "2026-11-06T23:59:59+0000"),
    ],
    "paging": {
        "cursors": {"after": "AFTER2"},
        "next": f"{MEDIA_URL}?fields=id&limit=50&after=AFTER2&access_token={TOKEN}",
    },
}


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with httpx.Client() as client:
        yield client


def make_reader(client: httpx.Client, sleeps: list[float] | None = None) -> MetaReader:
    return MetaReader(
        client,
        GRAPH + "/",
        IG_USER,
        TOKEN,
        HTTP,
        sleep=(sleeps if sleeps is not None else []).append,
        clock=lambda: NOW,
    )


def media_pages(request: httpx.Request) -> httpx.Response:
    pages = {None: PAGE_1, "AFTER1": PAGE_2}
    after = request.url.params.get("after")
    if after not in pages:
        return httpx.Response(500, json={"error": {"message": "page should not be requested"}})
    return httpx.Response(200, json=pages[after])


def test_platform_is_ig_trial(client: httpx.Client) -> None:
    assert make_reader(client).platform == Platform.IG_TRIAL


@respx.mock
def test_recent_videos_pages_through_reels_since_the_given_time(client: httpx.Client) -> None:
    route = respx.get(MEDIA_URL).mock(side_effect=media_pages)

    videos = make_reader(client).recent_videos(SINCE)

    assert videos == [
        PlatformVideo(
            platform=Platform.IG_TRIAL,
            external_id="103",
            permalink="https://www.instagram.com/reel/code103/",
            posted_at=datetime(2026, 11, 9, 1, 30, tzinfo=UTC),
            caption="Night Drive #fyp",
            duration_s=None,
        ),
        PlatformVideo(
            platform=Platform.IG_TRIAL,
            external_id="101",
            permalink="https://www.instagram.com/reel/code101/",
            posted_at=datetime(2026, 11, 8, 17, 5, tzinfo=UTC),
            caption="",
            duration_s=None,
        ),
        PlatformVideo(
            platform=Platform.IG_TRIAL,
            external_id="100",
            permalink="https://www.instagram.com/reel/code100/",
            posted_at=SINCE,
            caption="Night Drive #fyp",
            duration_s=None,
        ),
    ]
    assert route.call_count == 2
    first = route.calls[0].request.url.params
    assert first["fields"] == "id,permalink,timestamp,caption,media_type,media_product_type"
    assert first["access_token"] == TOKEN
    assert all(call.request.method == "GET" for call in route.calls)


@respx.mock
def test_recent_videos_stops_when_there_is_no_next_page(client: httpx.Client) -> None:
    last_page = {"data": [media("103", "2026-11-09T01:30:00+0000")], "paging": {}}
    route = respx.get(MEDIA_URL).mock(return_value=httpx.Response(200, json=last_page))
    assert [v.external_id for v in make_reader(client).recent_videos(SINCE)] == ["103"]
    assert route.call_count == 1


@respx.mock
def test_recent_videos_retries_a_429_with_backoff(client: httpx.Client) -> None:
    respx.get(MEDIA_URL).mock(
        side_effect=[
            httpx.Response(429, json={"error": {"message": "rate limited"}}),
            httpx.Response(200, json={"data": [media("103", "2026-11-09T01:30:00+0000")]}),
        ]
    )
    sleeps: list[float] = []
    videos = make_reader(client, sleeps).recent_videos(SINCE)
    assert [v.external_id for v in videos] == ["103"]
    assert sleeps == [HTTP.backoff_base_s]


@respx.mock
def test_an_api_error_raises_platform_read_error_without_the_token(client: httpx.Client) -> None:
    respx.get(MEDIA_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Invalid OAuth access token.", "code": 190}}
        )
    )
    with pytest.raises(PlatformReadError, match="HTTP 400: Invalid OAuth access token") as info:
        make_reader(client).recent_videos(SINCE)
    assert TOKEN not in str(info.value)
    assert info.value.__cause__ is None


def insight(name: str, value: float) -> dict[str, Any]:
    return {
        "name": name,
        "period": "lifetime",
        "values": [{"value": value}],
        "title": name,
        "id": f"1790/insights/{name}/lifetime",
    }


@respx.mock
def test_fetch_metrics_maps_reel_insights_and_keeps_the_raw_payload(
    client: httpx.Client,
) -> None:
    body = {
        "data": [
            insight("views", 1520),
            insight("reach", 1210),
            insight("saved", 14),
            insight("shares", 9),
            insight("ig_reels_avg_watch_time", 4350),
            insight("reels_skip_rate", 42.5),
        ]
    }
    route = respx.get(f"{GRAPH}/1790/insights").mock(return_value=httpx.Response(200, json=body))

    snapshot = make_reader(client).fetch_metrics("1790")

    assert snapshot == MetricSnapshot(
        fetched_at=NOW,
        views=1520,
        reach=1210,
        saves=14,
        shares=9,
        avg_watch_s=4.35,
        skip_rate=0.425,
        raw=body,
    )
    params = route.calls.last.request.url.params
    assert params["metric"] == "views,reach,saved,shares,ig_reels_avg_watch_time,reels_skip_rate"
    assert params["access_token"] == TOKEN


@respx.mock
def test_fetch_metrics_leaves_missing_metrics_as_none(client: httpx.Client) -> None:
    body = {"data": [insight("views", 30), {"name": "reach", "values": []}]}
    respx.get(f"{GRAPH}/1790/insights").mock(return_value=httpx.Response(200, json=body))

    snapshot = make_reader(client).fetch_metrics("1790")

    assert (snapshot.views, snapshot.reach, snapshot.saves, snapshot.avg_watch_s) == (
        30,
        None,
        None,
        None,
    )
    assert snapshot.skip_rate is None
    assert snapshot.raw == body

"""Read-only Instagram reader over the Meta Graph API (DESIGN.md §7.6)."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from engine.config import HttpSettings
from engine.db.enums import Platform
from engine.measure.api import request_json
from engine.measure.models import MetricSnapshot, PlatformVideo

MEDIA_FIELDS = "id,permalink,timestamp,caption,media_type,media_product_type"
INSIGHT_METRICS = "views,reach,saved,shares,ig_reels_avg_watch_time,reels_skip_rate"
PAGE_SIZE = 50


class MetaReader:
    """`PlatformReader` for the owner's Instagram professional account. Only GET calls."""

    def __init__(
        self,
        client: httpx.Client,
        graph_url: str,
        ig_user_id: str,
        access_token: str,
        http: HttpSettings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.graph_url = graph_url.rstrip("/")
        self.ig_user_id = ig_user_id
        self.access_token = access_token
        self.http = http
        self.sleep = sleep
        self.clock = clock

    @property
    def platform(self) -> Platform:
        return Platform.IG_TRIAL

    def recent_videos(self, since: datetime) -> list[PlatformVideo]:
        """Reels posted at or after `since`. The media list is newest first, so paging stops at
        the first item older than `since`. The Graph API has no media duration field."""
        videos: list[PlatformVideo] = []
        url: str | None = f"{self.graph_url}/{self.ig_user_id}/media"
        params: dict[str, Any] | None = {
            "fields": MEDIA_FIELDS,
            "limit": PAGE_SIZE,
            "access_token": self.access_token,
        }
        while url is not None:
            page = self._get(url, params)
            for item in page.get("data", []):
                posted_at = datetime.fromisoformat(item["timestamp"])
                if posted_at < since:
                    return videos
                if item.get("media_product_type") == "REELS":
                    videos.append(
                        PlatformVideo(
                            platform=self.platform,
                            external_id=item["id"],
                            permalink=item.get("permalink"),
                            posted_at=posted_at,
                            caption=item.get("caption") or "",
                        )
                    )
            url = page.get("paging", {}).get("next")
            params = None
        return videos

    def fetch_metrics(self, external_id: str) -> MetricSnapshot:
        body = self._get(
            f"{self.graph_url}/{external_id}/insights",
            {"metric": INSIGHT_METRICS, "access_token": self.access_token},
        )
        values = {
            entry["name"]: entry["values"][0]["value"]
            for entry in body.get("data", [])
            if entry.get("values")
        }
        avg_watch_ms = values.get("ig_reels_avg_watch_time")
        skip_percent = values.get("reels_skip_rate")
        return MetricSnapshot(
            fetched_at=self.clock(),
            views=values.get("views"),
            reach=values.get("reach"),
            saves=values.get("saved"),
            shares=values.get("shares"),
            avg_watch_s=avg_watch_ms / 1000 if avg_watch_ms is not None else None,
            skip_rate=skip_percent / 100 if skip_percent is not None else None,
            raw=body,
        )

    def _get(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        """`paging.next` URLs already carry every query parameter, so they get `params=None`."""
        return request_json(
            self.client, "GET", url, service="Meta", http=self.http, sleep=self.sleep, params=params
        )

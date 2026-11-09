"""Read-only TikTok reader over the Display API (DESIGN.md §7.6)."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from engine.config import HttpSettings
from engine.db.enums import Platform
from engine.measure.api import PlatformReadError, request_json
from engine.measure.models import MetricSnapshot, PlatformVideo

LIST_FIELDS = (
    "id,create_time,video_description,duration,share_url,"
    "view_count,like_count,comment_count,share_count"
)
METRIC_FIELDS = "id,view_count,like_count,comment_count,share_count"
PAGE_SIZE = 20


class TikTokReader:
    """`PlatformReader` for the owner's TikTok account (scopes `user.info.basic`, `video.list`).

    The Display API's list and query endpoints are POSTs, but both only read."""

    def __init__(
        self,
        client: httpx.Client,
        api_url: str,
        access_token: str,
        http: HttpSettings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.api_url = api_url.rstrip("/")
        self.access_token = access_token
        self.http = http
        self.sleep = sleep
        self.clock = clock

    @property
    def platform(self) -> Platform:
        return Platform.TIKTOK

    def recent_videos(self, since: datetime) -> list[PlatformVideo]:
        """Videos posted at or after `since`. The list is newest first, so paging stops at the
        first video older than `since`."""
        videos: list[PlatformVideo] = []
        request_body: dict[str, Any] = {"max_count": PAGE_SIZE}
        while True:
            data = self._post("/video/list/", LIST_FIELDS, request_body)["data"]
            for item in data.get("videos", []):
                posted_at = datetime.fromtimestamp(item["create_time"], UTC)
                if posted_at < since:
                    return videos
                duration = item.get("duration")
                videos.append(
                    PlatformVideo(
                        platform=self.platform,
                        external_id=item["id"],
                        permalink=item.get("share_url"),
                        posted_at=posted_at,
                        caption=item.get("video_description") or "",
                        duration_s=float(duration) if duration is not None else None,
                    )
                )
            if not data.get("has_more"):
                return videos
            request_body = {"max_count": PAGE_SIZE, "cursor": data["cursor"]}

    def fetch_metrics(self, external_id: str) -> MetricSnapshot:
        body = {"filters": {"video_ids": [external_id]}}
        response = self._post("/video/query/", METRIC_FIELDS, body)
        found = [v for v in response["data"].get("videos", []) if v.get("id") == external_id]
        if not found:
            raise PlatformReadError(f"TikTok has no video {external_id} for this account")
        video = found[0]
        return MetricSnapshot(
            fetched_at=self.clock(),
            views=video.get("view_count"),
            likes=video.get("like_count"),
            comments=video.get("comment_count"),
            shares=video.get("share_count"),
            raw=response,
        )

    def _post(self, path: str, fields: str, body: dict[str, Any]) -> dict[str, Any]:
        response = request_json(
            self.client,
            "POST",
            self.api_url + path,
            service="TikTok",
            http=self.http,
            sleep=self.sleep,
            params={"fields": fields},
            json=body,
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        error = response.get("error") or {}
        if error.get("code", "ok") != "ok":
            raise PlatformReadError(f"TikTok error {error['code']}: {error.get('message', '')}")
        return response

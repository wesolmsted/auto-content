from collections.abc import Iterable
from datetime import datetime

from engine.db.enums import Platform
from engine.measure.api import PlatformReadError
from engine.measure.models import MetricSnapshot, PlatformVideo


class FakePlatformReader:
    """In-memory `PlatformReader`.

    `videos` is the account's video list; `recent_videos(since)` returns those posted at or after
    `since`. `metrics[external_id]` is what `fetch_metrics` returns; an unknown id raises
    PlatformReadError, as do calls while `fail_listing` is set or for ids in `fail_metrics`.
    Every call is recorded in `listed_since` / `fetched`."""

    def __init__(
        self,
        platform: Platform,
        videos: Iterable[PlatformVideo] = (),
        metrics: dict[str, MetricSnapshot] | None = None,
    ) -> None:
        self._platform = platform
        self.videos = list(videos)
        self.metrics = dict(metrics or {})
        self.fail_listing = False
        self.fail_metrics: set[str] = set()
        self.listed_since: list[datetime] = []
        self.fetched: list[str] = []

    @property
    def platform(self) -> Platform:
        return self._platform

    def recent_videos(self, since: datetime) -> list[PlatformVideo]:
        self.listed_since.append(since)
        if self.fail_listing:
            raise PlatformReadError("fake reader told to fail listing")
        return [video for video in self.videos if video.posted_at >= since]

    def fetch_metrics(self, external_id: str) -> MetricSnapshot:
        self.fetched.append(external_id)
        if external_id in self.fail_metrics or external_id not in self.metrics:
            raise PlatformReadError(f"fake reader has no metrics for {external_id}")
        return self.metrics[external_id]

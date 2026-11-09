from datetime import datetime
from typing import Protocol

from engine.db.enums import Platform
from engine.measure.models import MetricSnapshot, PlatformVideo


class PlatformReader(Protocol):
    """Read-only access to the owner's posts on one platform.

    Deliberately has no write methods: the system never posts (DESIGN.md §2.6).
    """

    @property
    def platform(self) -> Platform: ...

    def recent_videos(self, since: datetime) -> list[PlatformVideo]:
        """The owner's videos posted at or after `since`."""
        ...

    def fetch_metrics(self, external_id: str) -> MetricSnapshot:
        """Current metrics for one posted video."""
        ...

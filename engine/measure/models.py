from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engine.db.enums import Platform


class PlatformVideo(BaseModel):
    """A video the owner posted, as listed by a platform's read-only API."""

    model_config = ConfigDict(frozen=True)

    platform: Platform
    external_id: str
    permalink: str | None = None
    posted_at: datetime
    caption: str = ""
    duration_s: float | None = None


class MetricSnapshot(BaseModel):
    """Metrics for one post at one point in time. Fields a platform doesn't return stay None."""

    model_config = ConfigDict(frozen=True)

    fetched_at: datetime
    views: int | None = None
    reach: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    avg_watch_s: float | None = None
    full_watch_rate: float | None = None
    skip_rate: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class AwaitingPost(BaseModel):
    """A `posts` row in `awaiting_post`, with what matching needs from its render."""

    model_config = ConfigDict(frozen=True)

    post_id: int
    platform: Platform
    downloaded_at: datetime
    duration_s: float
    caption: str


class Match(BaseModel):
    """A post linked to the platform video the owner posted."""

    model_config = ConfigDict(frozen=True)

    post_id: int
    external_id: str
    permalink: str | None = None
    posted_at: datetime
    score: float

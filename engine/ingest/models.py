from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from engine.db.enums import Orientation


class QualityIssue(StrEnum):
    SHAKY = "shaky"
    BLURRY = "blurry"
    DULL = "dull"


class ShotTags(BaseModel):
    """Semantic tags for one shot from the vision model.

    The string fields take values from the closed vocabulary in `vision.vocabulary` config;
    the tagger rejects anything outside it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str
    subject: str
    setting: str
    lighting: str
    mood: str
    has_face: bool
    quality: float = Field(ge=0, le=1)
    issues: tuple[QualityIssue, ...] = ()


class Shot(BaseModel):
    """A tagged span of a footage file, with its 9:16-ready proxy. `id` is None until stored.

    `start_s`/`end_s` are positions in the original footage file; the proxy starts at 0.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None
    footage_file_id: int
    start_s: float = Field(ge=0)
    end_s: float
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    orientation: Orientation
    motion_score: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=1)
    tags: ShotTags
    proxy_path: Path
    thumbnail_path: Path | None = None
    use_count: int = 0
    last_used_at: datetime | None = None

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class PollSummary(BaseModel):
    """Counts from one `poll_footage` run."""

    model_config = ConfigDict(frozen=True)

    listed: int = 0
    new: int = 0
    duplicates: int = 0
    downloaded: int = 0
    completed: int = 0
    failed: int = 0

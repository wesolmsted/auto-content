"""Contract models for micro edits (DESIGN.md §7.10)."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Sidecar(BaseModel):
    """A micro set's parsed `.txt`: the song name, the optional post caption (kept for future
    auto-uploads), the optional time on the source video when the on-screen text disappears,
    and the on-screen texts."""

    model_config = ConfigDict(frozen=True)

    song_title: str = Field(min_length=1)
    caption: str | None = Field(default=None, min_length=1)
    text_cutoff_s: float | None = Field(default=None, gt=0)
    text_options: tuple[str, ...] = Field(min_length=1)


class MicroTextPosition(StrEnum):
    TOP = "top"
    CENTER = "center"


class MicroEdit(BaseModel):
    """The small edits that make one micro render differ from its source. Stored as
    `micro_renders.edit_json`.

    `crop_x`/`crop_y` place the zoomed crop window inside the zoom margin: 0 is the left/top
    edge, 0.5 centered, 1 the right/bottom edge. `text_y_offset` shifts the text by that
    fraction of the frame height (positive is down). `text_cutoff_s` is the time on the
    source video when the text disappears; None keeps it for the whole render.
    """

    model_config = ConfigDict(frozen=True)

    trim_start_s: float = Field(ge=0)
    trim_end_s: float = Field(ge=0)
    speed: float = Field(gt=0)
    zoom: float = Field(ge=1)
    crop_x: float = Field(ge=0, le=1)
    crop_y: float = Field(ge=0, le=1)
    mirror: bool
    brightness: float
    contrast: float = Field(gt=0)
    saturation: float = Field(ge=0)
    hue_degrees: float
    text: str = Field(min_length=1)
    text_position: MicroTextPosition
    text_size_fraction: float = Field(gt=0)
    text_y_offset: float
    text_cutoff_s: float | None = Field(default=None, gt=0)


class MicroSourceMedia(BaseModel):
    """The local copy of a micro source, as the renderer needs it."""

    model_config = ConfigDict(frozen=True)

    path: Path
    duration_s: float = Field(gt=0)
    has_audio: bool


class MicroIngestSummary(BaseModel):
    """What one `poll_micro_sets` run did. Set counts first, then video counts."""

    model_config = ConfigDict(frozen=True)

    sets: int = 0
    new_sets: int = 0
    waiting_sets: int = 0
    removed_sets: int = 0
    new_videos: int = 0
    ready_videos: int = 0
    duplicate_videos: int = 0
    removed_videos: int = 0
    failed_videos: int = 0

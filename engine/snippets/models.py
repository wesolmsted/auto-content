"""Song analysis and per-snippet beat grid models."""

from itertools import pairwise
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _check_beat_times(
    beats: tuple[float, ...], downbeats: tuple[float, ...], duration_s: float
) -> None:
    if any(later <= earlier for earlier, later in pairwise(beats)):
        raise ValueError("beats must be strictly ascending")
    if beats and (beats[0] < 0 or beats[-1] > duration_s):
        raise ValueError("beats must lie within [0, duration_s]")
    if not set(downbeats) <= set(beats):
        raise ValueError("every downbeat must also be a beat")


class SongAnalysis(BaseModel):
    """Whole-song analysis, in seconds from the start of the song audio. Stored on `songs`.

    Beats are strictly ascending and within [0, duration_s]; every downbeat is also a beat.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    duration_s: float = Field(gt=0)
    bpm: float = Field(gt=0)
    beats: tuple[float, ...]
    downbeats: tuple[float, ...]

    @model_validator(mode="after")
    def _beats_are_consistent(self) -> Self:
        _check_beat_times(self.beats, self.downbeats, self.duration_s)
        return self


class BeatGrid(BaseModel):
    """Beat grid for one snippet window. All times are seconds from the snippet start.

    Beats are strictly ascending and within [0, duration_s]; every downbeat is also a beat.
    `drop_s` is None when the snippet has no drop.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bpm: float = Field(gt=0)
    beats: tuple[float, ...]
    downbeats: tuple[float, ...]
    drop_s: float | None = None
    duration_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _times_are_consistent(self) -> Self:
        _check_beat_times(self.beats, self.downbeats, self.duration_s)
        if self.drop_s is not None and not 0 <= self.drop_s <= self.duration_s:
            raise ValueError("drop_s must lie within [0, duration_s]")
        return self

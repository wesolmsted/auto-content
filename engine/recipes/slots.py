"""Cut points and slots for a recipe on a beat grid, shared by shot selection and assembly."""

from itertools import pairwise
from typing import NamedTuple

from engine.recipes.models import CutRate, Recipe
from engine.snippets.models import BeatGrid

MIN_SEGMENT_S = 0.1
"""No cut lands closer than this to another cut, the start or the end of the video."""


class NotEnoughShotsError(ValueError):
    """The available shots can't fill every slot of the timeline."""


class Slot(NamedTuple):
    start_s: float
    end_s: float

    @property
    def length_s(self) -> float:
        return self.end_s - self.start_s


def plan_slots(recipe: Recipe, beat_grid: BeatGrid) -> list[Slot]:
    """The output-time slots between cuts, from 0 to the snapped video length."""
    duration_s = target_duration(recipe.length_s, beat_grid)
    cuts = cut_points(recipe, beat_grid, duration_s)
    return [Slot(start, end) for start, end in pairwise([0.0, *cuts, duration_s])]


def target_duration(length_s: float, beat_grid: BeatGrid) -> float:
    """`length_s` snapped to the nearest beat after 0 (the earlier beat on a tie)."""
    beats = [beat for beat in beat_grid.beats if beat > 0]
    if not beats:
        return min(length_s, beat_grid.duration_s)
    return min(beats, key=lambda beat: abs(beat - length_s))


def cut_points(recipe: Recipe, beat_grid: BeatGrid, duration_s: float) -> list[float]:
    def inside(time_s: float) -> bool:
        return MIN_SEGMENT_S <= time_s <= duration_s - MIN_SEGMENT_S

    cuts = [time_s for time_s in _grid_cuts(recipe.cut_rate, beat_grid) if inside(time_s)]
    drop_s = beat_grid.drop_s
    if recipe.hard_cut_on_drop and drop_s is not None and inside(drop_s):
        cuts = [time_s for time_s in cuts if abs(time_s - drop_s) >= MIN_SEGMENT_S]
        cuts = sorted([*cuts, drop_s])
    return cuts


def _grid_cuts(cut_rate: CutRate, beat_grid: BeatGrid) -> tuple[float, ...]:
    match cut_rate:
        case CutRate.EVERY_BEAT:
            return beat_grid.beats
        case CutRate.EVERY_2_BEATS:
            return beat_grid.beats[::2]
        case CutRate.DOWNBEATS:
            return beat_grid.downbeats
        case CutRate.ONE_LONG_SHOT:
            return ()

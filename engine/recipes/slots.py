"""Cut points and slots for a recipe on a beat grid, shared by shot selection and assembly."""

from collections.abc import Sequence
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


def fit_long_shots(
    recipe: Recipe, beat_grid: BeatGrid, shot_durations: Sequence[float]
) -> list[tuple[int, Slot]]:
    """`one_long_shot` slots: each shot runs as long as it can, cut on the last beat it covers.

    The video is split at its forced cuts (the drop, with `hard_cut_on_drop`) and filled from
    0 onwards. Each slot takes the first unused shot, in the given order, that reaches the next
    forced cut or a beat at least `MIN_SEGMENT_S` in. Returns `(index into shot_durations,
    slot)` pairs in timeline order. Raises `NotEnoughShotsError` when no unused shot fits.
    """
    duration_s = target_duration(recipe.length_s, beat_grid)
    forced_cuts = [0.0, *cut_points(recipe, beat_grid, duration_s), duration_s]
    unused = list(range(len(shot_durations)))
    fitted: list[tuple[int, Slot]] = []
    for section_start, section_end in pairwise(forced_cuts):
        start = section_start
        while start < section_end:
            fit = _first_fitting_shot(start, section_end, unused, shot_durations, beat_grid)
            if fit is None:
                raise NotEnoughShotsError(
                    f"recipe {recipe.id}: no unused shot reaches a beat after {start:.2f} s "
                    f"(slot {len(fitted) + 1})"
                )
            index, end = fit
            unused.remove(index)
            fitted.append((index, Slot(start, end)))
            start = end
    return fitted


def _first_fitting_shot(
    start_s: float,
    section_end_s: float,
    unused: list[int],
    shot_durations: Sequence[float],
    beat_grid: BeatGrid,
) -> tuple[int, float] | None:
    for index in unused:
        end_s = _long_shot_end(start_s, shot_durations[index], section_end_s, beat_grid)
        if end_s is not None:
            return index, end_s
    return None


def _long_shot_end(
    start_s: float, shot_duration_s: float, section_end_s: float, beat_grid: BeatGrid
) -> float | None:
    reach_s = start_s + shot_duration_s
    if reach_s >= section_end_s:
        return section_end_s
    last_cut_s = min(reach_s, section_end_s - MIN_SEGMENT_S)
    covered = [beat for beat in beat_grid.beats if start_s + MIN_SEGMENT_S <= beat <= last_cut_s]
    return covered[-1] if covered else None


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

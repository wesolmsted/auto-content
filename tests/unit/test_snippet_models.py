from typing import Any

import pytest
from pydantic import ValidationError

from engine.snippets.models import BeatGrid, SongAnalysis


def grid(**overrides: Any) -> BeatGrid:
    params: dict[str, Any] = {
        "bpm": 120.0,
        "beats": (0.0, 0.5, 1.0, 1.5, 2.0),
        "downbeats": (0.0, 2.0),
        "drop_s": 2.0,
        "duration_s": 2.0,
    }
    return BeatGrid.model_validate(params | overrides)


def test_valid_grid_keeps_its_times() -> None:
    beat_grid = grid()
    assert beat_grid.beats == (0.0, 0.5, 1.0, 1.5, 2.0)
    assert beat_grid.downbeats == (0.0, 2.0)
    assert beat_grid.drop_s == 2.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"beats": (0.0, 1.0, 0.5, 1.5)},
        {"beats": (0.0, 0.5, 0.5, 1.0)},
        {"beats": (-0.1, 0.5)},
        {"beats": (0.0, 0.5, 2.5), "downbeats": (0.0,)},
        {"downbeats": (0.0, 1.25)},
        {"drop_s": 2.1},
        {"drop_s": -0.5},
    ],
    ids=[
        "beats_unsorted",
        "beats_duplicate",
        "beat_before_start",
        "beat_after_end",
        "downbeat_not_a_beat",
        "drop_after_end",
        "drop_before_start",
    ],
)
def test_grid_rejects_inconsistent_times(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        grid(**overrides)


def test_snippet_without_drop_and_without_beats_is_valid() -> None:
    beat_grid = grid(beats=(), downbeats=(), drop_s=None)
    assert beat_grid.beats == ()
    assert beat_grid.drop_s is None


def test_song_analysis_applies_the_same_beat_rules() -> None:
    with pytest.raises(ValidationError):
        SongAnalysis(duration_s=10.0, bpm=120.0, beats=(0.0, 0.5), downbeats=(0.25,))
    with pytest.raises(ValidationError):
        SongAnalysis(duration_s=10.0, bpm=120.0, beats=(1.0, 0.5), downbeats=())

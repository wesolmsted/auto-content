import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.snippets.analysis import beat_grid_for_snippet
from engine.snippets.models import SongAnalysis

SONG_120_BPM = SongAnalysis(
    duration_s=20.0,
    bpm=120.0,
    beats=tuple(beat * 0.5 for beat in range(40)),
    downbeats=tuple(bar * 2.0 for bar in range(10)),
)


def test_grid_is_shifted_to_the_snippet_start_and_clipped_to_the_window() -> None:
    grid = beat_grid_for_snippet(SONG_120_BPM, start_s=7.0, end_s=10.0, drop_s=8.0)

    assert grid.bpm == 120.0
    assert grid.duration_s == 3.0
    assert grid.beats == (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
    assert grid.downbeats == (1.0, 3.0)
    assert grid.drop_s == 1.0


def test_window_between_beats_keeps_only_inner_beats_and_no_drop() -> None:
    grid = beat_grid_for_snippet(SONG_120_BPM, start_s=7.25, end_s=8.75, drop_s=None)

    assert grid.duration_s == 1.5
    assert grid.beats == (0.25, 0.75, 1.25)
    assert grid.downbeats == (0.75,)
    assert grid.drop_s is None


def test_drop_outside_the_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="drop"):
        beat_grid_for_snippet(SONG_120_BPM, start_s=10.0, end_s=14.0, drop_s=8.0)


def test_empty_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="end_s"):
        beat_grid_for_snippet(SONG_120_BPM, start_s=10.0, end_s=10.0, drop_s=None)


@st.composite
def songs(draw: st.DrawFn) -> SongAnalysis:
    intervals = draw(st.lists(st.floats(0.2, 2.0), min_size=0, max_size=60))
    first = draw(st.floats(0.0, 2.0))
    beats = tuple(itertools.accumulate(intervals, initial=first))
    tail = draw(st.floats(0.0, 2.0))
    phase = draw(st.integers(0, 3))
    return SongAnalysis(
        duration_s=beats[-1] + tail + 0.1,
        bpm=draw(st.floats(60.0, 200.0)),
        beats=beats,
        downbeats=beats[phase::4],
    )


@st.composite
def snippet_windows(draw: st.DrawFn) -> tuple[SongAnalysis, float, float, float | None]:
    song = draw(songs())
    start_s = draw(st.floats(0.0, song.duration_s - 0.05))
    end_s = draw(st.floats(start_s + 0.01, song.duration_s))
    drop_s = draw(st.none() | st.floats(start_s, end_s))
    return song, start_s, end_s, drop_s


@given(snippet_windows())
def test_grid_invariants(window: tuple[SongAnalysis, float, float, float | None]) -> None:
    song, start_s, end_s, drop_s = window
    grid = beat_grid_for_snippet(song, start_s, end_s, drop_s)

    assert grid.duration_s == pytest.approx(end_s - start_s)
    assert all(0.0 <= beat <= grid.duration_s for beat in grid.beats)
    assert all(earlier < later for earlier, later in itertools.pairwise(grid.beats))
    assert set(grid.downbeats) <= set(grid.beats)
    inside = [beat for beat in song.beats if start_s <= beat <= end_s]
    assert len(grid.beats) == len(inside)
    assert grid.beats == pytest.approx([beat - start_s for beat in inside])
    if drop_s is None:
        assert grid.drop_s is None
    else:
        assert grid.drop_s is not None
        assert 0.0 <= grid.drop_s <= grid.duration_s
        assert grid.drop_s == pytest.approx(drop_s - start_s)

import pytest

from engine.snippets.analysis import snap_start_to_downbeat

DOWNBEATS = (2.0, 4.0, 6.0, 8.0)


def test_start_exactly_on_a_downbeat_is_kept() -> None:
    assert snap_start_to_downbeat(DOWNBEATS, start_s=4.0, end_s=10.0) == 4.0


def test_start_moves_to_the_nearest_downbeat_in_either_direction() -> None:
    assert snap_start_to_downbeat(DOWNBEATS, start_s=4.4, end_s=10.0) == 4.0
    assert snap_start_to_downbeat(DOWNBEATS, start_s=5.6, end_s=10.0) == 6.0


def test_tie_between_two_downbeats_picks_the_earlier() -> None:
    assert snap_start_to_downbeat(DOWNBEATS, start_s=5.0, end_s=10.0) == 4.0


def test_start_before_the_first_downbeat_moves_forward_to_it() -> None:
    assert snap_start_to_downbeat(DOWNBEATS, start_s=0.3, end_s=10.0) == 2.0


def test_nearest_downbeat_at_end_falls_back_to_the_nearest_one_before_end() -> None:
    assert snap_start_to_downbeat(DOWNBEATS, start_s=5.6, end_s=6.0) == 4.0


def test_nearest_downbeat_past_end_falls_back_to_the_nearest_one_before_end() -> None:
    assert snap_start_to_downbeat(DOWNBEATS, start_s=5.5, end_s=5.8) == 4.0


def test_no_downbeat_before_end_is_rejected() -> None:
    with pytest.raises(ValueError, match="downbeat"):
        snap_start_to_downbeat(DOWNBEATS, start_s=0.5, end_s=1.8)


def test_song_without_downbeats_is_rejected() -> None:
    with pytest.raises(ValueError, match="downbeat"):
        snap_start_to_downbeat((), start_s=1.0, end_s=5.0)

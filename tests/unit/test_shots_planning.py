from itertools import pairwise

import pytest

from engine.db.enums import Orientation
from engine.ingest.frames import frame_time_s, sample_frame_indices
from engine.ingest.proxies import fit_width, vertical_crop
from engine.ingest.scenes import ShotSpan, plan_shot_spans
from engine.ingest.shots import display_size, orientation_for

FPS = 30.0


def test_scenes_within_limits_are_kept_unchanged() -> None:
    scenes = [ShotSpan(0, 75), ShotSpan(75, 150)]
    assert plan_shot_spans(scenes, FPS, min_shot_s=0.8, max_shot_s=8.0) == scenes


def test_scenes_shorter_than_min_are_dropped() -> None:
    scenes = [ShotSpan(0, 23), ShotSpan(23, 24), ShotSpan(24, 120)]
    assert plan_shot_spans(scenes, FPS, min_shot_s=0.8, max_shot_s=8.0) == [ShotSpan(24, 120)]


def test_a_scene_exactly_min_long_is_kept() -> None:
    assert plan_shot_spans([ShotSpan(0, 24)], FPS, min_shot_s=0.8, max_shot_s=8.0) == [
        ShotSpan(0, 24)
    ]


def test_a_scene_exactly_max_long_is_not_split() -> None:
    assert plan_shot_spans([ShotSpan(0, 240)], FPS, min_shot_s=0.8, max_shot_s=8.0) == [
        ShotSpan(0, 240)
    ]


def test_long_scenes_split_into_equal_contiguous_sub_shots_no_longer_than_max() -> None:
    spans = plan_shot_spans([ShotSpan(30, 30 + 600)], FPS, min_shot_s=0.8, max_shot_s=8.0)
    assert spans == [ShotSpan(30, 230), ShotSpan(230, 430), ShotSpan(430, 630)]


def test_uneven_split_keeps_every_frame_and_differs_by_at_most_one_frame() -> None:
    spans = plan_shot_spans([ShotSpan(0, 250)], FPS, min_shot_s=0.8, max_shot_s=4.0)
    assert spans[0].start_frame == 0
    assert spans[-1].end_frame == 250
    assert all(a.end_frame == b.start_frame for a, b in pairwise(spans))
    lengths = [span.end_frame - span.start_frame for span in spans]
    assert len(spans) == 3
    assert max(lengths) - min(lengths) <= 1
    assert max(lengths) <= 4.0 * FPS


def test_no_scenes_gives_no_shots() -> None:
    assert plan_shot_spans([], FPS, min_shot_s=0.8, max_shot_s=8.0) == []


def test_three_sample_frames_are_first_middle_and_last() -> None:
    assert sample_frame_indices(ShotSpan(75, 150), 3) == [75, 112, 149]


def test_one_sample_frame_is_the_middle() -> None:
    assert sample_frame_indices(ShotSpan(0, 10), 1) == [5]


def test_sample_frames_of_a_short_span_stay_inside_it() -> None:
    assert sample_frame_indices(ShotSpan(10, 12), 3) == [10, 11, 11]


def test_frame_time_never_lands_after_the_frame() -> None:
    assert frame_time_s(75, 30.0) == 2.5
    assert frame_time_s(25, 30.0) == pytest.approx(0.833, abs=1e-9)
    assert frame_time_s(25, 30.0) <= 25 / 30.0
    assert frame_time_s(1001, 29.97) <= 1001 / 29.97


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1080, 1920, (1080, 1920)),
        (1920, 1080, (606, 1080)),
        (1280, 720, (404, 720)),
        (1080, 1080, (606, 1080)),
        (1080, 2400, (1080, 1920)),
        (721, 1281, (720, 1280)),
    ],
)
def test_vertical_crop_is_the_largest_centered_9_16_even_box(
    width: int, height: int, expected: tuple[int, int]
) -> None:
    assert vertical_crop(width, height) == expected


@pytest.mark.parametrize(
    ("size", "max_width", "expected"),
    [
        ((1080, 1920), 1080, (1080, 1920)),
        ((2160, 3840), 1080, (1080, 1920)),
        ((404, 720), 1080, (404, 720)),
        ((1214, 2160), 720, (720, 1280)),
    ],
)
def test_fit_width_only_scales_down_and_keeps_even_dims(
    size: tuple[int, int], max_width: int, expected: tuple[int, int]
) -> None:
    assert fit_width(*size, max_width) == expected


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1080, 1920, Orientation.PORTRAIT),
        (1920, 1080, Orientation.LANDSCAPE),
        (1080, 1080, Orientation.SQUARE),
    ],
)
def test_orientation_follows_display_size(width: int, height: int, expected: Orientation) -> None:
    assert orientation_for(width, height) == expected


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [(0, (1920, 1080)), (90, (1080, 1920)), (-90, (1080, 1920)), (180, (1920, 1080))],
)
def test_display_size_swaps_coded_size_for_quarter_turns(
    rotation: int, expected: tuple[int, int]
) -> None:
    assert display_size(1920, 1080, rotation) == expected

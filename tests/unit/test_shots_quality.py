import pytest

from engine.ingest.quality import (
    FrameStats,
    ShotHeuristics,
    combine_quality,
    shot_heuristics,
)
from engine.ingest.scenes import ShotSpan

FPS = 30.0


def steady(flow_px: float, sharpness: float = 500.0, vector: tuple[float, float] = (0.0, 0.0)):
    return FrameStats(flow_px=flow_px, global_motion=vector, sharpness=sharpness)


def test_static_shot_has_zero_motion_and_no_shake() -> None:
    stats = [steady(0.0) for _ in range(30)]
    heuristics = shot_heuristics(stats, ShotSpan(0, 30), FPS)
    assert heuristics.motion_score == 0.0
    assert heuristics.shake == 0.0


def test_motion_score_grows_with_flow_and_saturates_at_one() -> None:
    slow = shot_heuristics([steady(0.5) for _ in range(30)], ShotSpan(0, 30), FPS)
    fast = shot_heuristics([steady(2.0) for _ in range(30)], ShotSpan(0, 30), FPS)
    huge = shot_heuristics([steady(50.0) for _ in range(30)], ShotSpan(0, 30), FPS)
    assert 0.0 < slow.motion_score < fast.motion_score < 1.0
    assert huge.motion_score == 1.0


def test_motion_is_normalized_per_second_not_per_frame() -> None:
    at_30 = shot_heuristics([steady(1.0) for _ in range(30)], ShotSpan(0, 30), 30.0)
    at_60 = shot_heuristics([steady(0.5) for _ in range(60)], ShotSpan(0, 60), 60.0)
    assert at_30.motion_score == pytest.approx(at_60.motion_score)


def test_flow_across_the_cut_into_the_shot_is_ignored() -> None:
    stats = [steady(0.0) for _ in range(60)]
    stats[30] = steady(80.0)
    heuristics = shot_heuristics(stats, ShotSpan(30, 60), FPS)
    assert heuristics.motion_score == 0.0


def test_jittery_global_motion_is_shakier_than_a_smooth_pan() -> None:
    pan = [steady(2.0, vector=(2.0, 0.0)) for _ in range(30)]
    jitter = [steady(2.0, vector=(2.0, 0.0) if index % 2 else (-2.0, 1.0)) for index in range(30)]
    smooth = shot_heuristics(pan, ShotSpan(0, 30), FPS)
    shaky = shot_heuristics(jitter, ShotSpan(0, 30), FPS)
    assert smooth.shake == 0.0
    assert 0.5 < shaky.shake <= 1.0


def test_sharpness_is_normalized_and_saturates() -> None:
    flat = shot_heuristics([steady(0.0, sharpness=0.0) for _ in range(10)], ShotSpan(0, 10), FPS)
    soft = shot_heuristics([steady(0.0, sharpness=40.0) for _ in range(10)], ShotSpan(0, 10), FPS)
    crisp = shot_heuristics([steady(0.0, sharpness=900.0) for _ in range(10)], ShotSpan(0, 10), FPS)
    assert flat.sharpness == 0.0
    assert 0.0 < soft.sharpness < 1.0
    assert crisp.sharpness == 1.0


def test_a_single_frame_shot_has_no_motion() -> None:
    heuristics = shot_heuristics([steady(9.0) for _ in range(5)], ShotSpan(2, 3), FPS)
    assert heuristics.motion_score == 0.0
    assert heuristics.shake == 0.0


def test_quality_blends_tagger_quality_with_local_sharpness_and_shake() -> None:
    crisp_steady = ShotHeuristics(motion_score=0.3, sharpness=1.0, shake=0.0)
    blurry = ShotHeuristics(motion_score=0.3, sharpness=0.0, shake=0.0)
    shaky = ShotHeuristics(motion_score=0.3, sharpness=1.0, shake=1.0)
    assert combine_quality(1.0, crisp_steady) == 1.0
    assert combine_quality(0.0, blurry) == 0.0
    assert combine_quality(0.8, crisp_steady) > combine_quality(0.8, blurry)
    assert combine_quality(0.8, blurry) == pytest.approx(combine_quality(0.8, shaky))
    assert combine_quality(0.9, blurry) > combine_quality(0.5, blurry)
    assert combine_quality(0.5, ShotHeuristics(motion_score=0.0, sharpness=0.5, shake=0.0)) == (
        pytest.approx(0.5)
    )

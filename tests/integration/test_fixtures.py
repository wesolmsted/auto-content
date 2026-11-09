"""The generated fixtures really have the ground truth other tests rely on."""

from pathlib import Path

import cv2
import numpy as np
import pytest
import soundfile
from scenedetect import ContentDetector, detect

from engine.ffmpeg import probe
from tests.fixtures import make_fixtures as fx

ONE_FRAME_S = 1 / fx.VIDEO_FPS


def test_portrait_clip_has_expected_size_and_duration(portrait_scenes_clip: Path) -> None:
    info = probe(portrait_scenes_clip)
    assert (info.width, info.height) == fx.PORTRAIT_SIZE
    assert info.fps == pytest.approx(fx.VIDEO_FPS)
    assert info.duration_s == pytest.approx(fx.PORTRAIT_DURATION_S, abs=ONE_FRAME_S)


def test_portrait_clip_scene_changes_are_detected_at_known_times(
    portrait_scenes_clip: Path,
) -> None:
    scenes = detect(str(portrait_scenes_clip), ContentDetector())
    cut_times = [start.seconds for start, _end in scenes[1:]]
    assert cut_times == pytest.approx(list(fx.PORTRAIT_SCENE_CHANGES_S), abs=ONE_FRAME_S)


def test_landscape_clip_has_motion_and_audio(landscape_motion_clip: Path) -> None:
    info = probe(landscape_motion_clip)
    assert (info.width, info.height) == fx.LANDSCAPE_SIZE
    assert info.duration_s == pytest.approx(fx.LANDSCAPE_DURATION_S, abs=0.05)
    assert info.has_audio

    capture = cv2.VideoCapture(str(landscape_motion_clip))
    frames = []
    for _ in range(16):
        ok, frame = capture.read()
        assert ok
        frames.append(frame.astype(np.float32))
    capture.release()
    assert np.abs(frames[15] - frames[0]).mean() > 1.0


def click_onsets_s(samples: np.ndarray, sample_rate: int) -> list[float]:
    loud = np.flatnonzero(np.abs(samples) > 0.05)
    starts = loud[np.insert(np.diff(loud) > sample_rate // 10, 0, True)]
    return [index / sample_rate for index in starts]


def test_click_track_is_exactly_120_bpm_with_accents_and_a_drop(click_track_wav: Path) -> None:
    samples, sample_rate = soundfile.read(click_track_wav)
    assert sample_rate == fx.CLICK_SAMPLE_RATE
    assert samples.ndim == 1
    assert len(samples) / sample_rate == pytest.approx(fx.CLICK_DURATION_S)

    pre_drop = samples[: int(fx.CLICK_DROP_S * sample_rate)]
    onsets = click_onsets_s(pre_drop, sample_rate)
    beat_count = int(fx.CLICK_DROP_S / fx.CLICK_BEAT_INTERVAL_S)
    expected = [beat * fx.CLICK_BEAT_INTERVAL_S for beat in range(beat_count)]
    assert onsets == pytest.approx(expected, abs=0.002)

    def peak_near(time_s: float) -> float:
        start = int(time_s * sample_rate)
        return float(np.abs(samples[start : start + sample_rate // 20]).max())

    assert peak_near(0.0) > 1.5 * peak_near(0.5)
    assert peak_near(2.0) > 1.5 * peak_near(2.5)

    def rms(start_s: float, end_s: float) -> float:
        window = samples[int(start_s * sample_rate) : int(end_s * sample_rate)]
        return float(np.sqrt(np.mean(window**2)))

    drop = fx.CLICK_DROP_S
    assert rms(drop, drop + 2.0) > 3 * rms(drop - 2.0, drop)
    assert drop / fx.CLICK_BEAT_INTERVAL_S % fx.CLICK_BEATS_PER_BAR == 0

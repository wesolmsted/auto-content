from pathlib import Path

import numpy as np
import pytest
import soundfile

from engine.config import SnippetSettings, load_settings
from engine.snippets.analysis import analyze_song, detect_drop
from engine.snippets.models import SongAnalysis
from tests.fixtures.make_fixtures import (
    CLICK_BEAT_INTERVAL_S,
    CLICK_BEATS_PER_BAR,
    CLICK_BPM,
    CLICK_DROP_S,
    CLICK_DURATION_S,
)

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
TOLERANCE_S = 0.020


@pytest.fixture(scope="module")
def settings() -> SnippetSettings:
    return load_settings(REPO_CONFIG_DIR, env_file=None).snippets


@pytest.fixture(scope="module")
def click_song(click_track_wav: Path, settings: SnippetSettings) -> SongAnalysis:
    return analyze_song(click_track_wav, settings)


def test_tempo_is_within_half_a_bpm(click_song: SongAnalysis) -> None:
    assert click_song.bpm == pytest.approx(CLICK_BPM, abs=0.5)
    assert click_song.duration_s == pytest.approx(CLICK_DURATION_S, abs=0.01)


def test_every_beat_of_the_track_is_found_on_the_grid(click_song: SongAnalysis) -> None:
    expected = np.arange(round(CLICK_DURATION_S / CLICK_BEAT_INTERVAL_S)) * CLICK_BEAT_INTERVAL_S

    assert len(click_song.beats) == len(expected)
    assert np.max(np.abs(np.array(click_song.beats) - expected)) <= TOLERANCE_S


def test_downbeats_are_the_accented_beats(click_song: SongAnalysis) -> None:
    bar_s = CLICK_BEAT_INTERVAL_S * CLICK_BEATS_PER_BAR
    expected = np.arange(round(CLICK_DURATION_S / bar_s)) * bar_s

    assert len(click_song.downbeats) == len(expected)
    assert np.max(np.abs(np.array(click_song.downbeats) - expected)) <= TOLERANCE_S


def test_drop_is_found_and_snapped_to_its_downbeat(
    click_track_wav: Path, click_song: SongAnalysis, settings: SnippetSettings
) -> None:
    drop_s = detect_drop(click_track_wav, click_song, 4.0, 14.0, settings)

    assert drop_s is not None
    assert drop_s == pytest.approx(CLICK_DROP_S, abs=TOLERANCE_S)
    assert drop_s in click_song.downbeats


def test_drop_is_found_with_the_whole_song_as_the_window(
    click_track_wav: Path, click_song: SongAnalysis, settings: SnippetSettings
) -> None:
    drop_s = detect_drop(click_track_wav, click_song, 0.0, CLICK_DURATION_S, settings)

    assert drop_s == pytest.approx(CLICK_DROP_S, abs=TOLERANCE_S)


def test_no_drop_in_a_steady_section(
    click_track_wav: Path, click_song: SongAnalysis, settings: SnippetSettings
) -> None:
    assert detect_drop(click_track_wav, click_song, 10.0, 18.0, settings) is None


def test_no_drop_in_a_flat_tone(tmp_path: Path, settings: SnippetSettings) -> None:
    rate = 22050
    time = np.arange(int(10.0 * rate)) / rate
    tone_path = tmp_path / "tone.wav"
    soundfile.write(tone_path, 0.2 * np.sin(2 * np.pi * 220.0 * time), rate)
    song = SongAnalysis(
        duration_s=10.0,
        bpm=120.0,
        beats=tuple(beat * 0.5 for beat in range(20)),
        downbeats=tuple(bar * 2.0 for bar in range(5)),
    )

    assert detect_drop(tone_path, song, 0.0, 10.0, settings) is None

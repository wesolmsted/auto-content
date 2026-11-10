from pathlib import Path

import librosa
import numpy as np
from numpy.typing import NDArray

from engine.config import SnippetSettings
from engine.snippets.models import BeatGrid, SongAnalysis

HOP_LENGTH = 256
SILENCE_TOP_DB = 60.0
MIN_DROP_JUMP_DB = 3.0
ENERGY_FLOOR = 1e-10

FloatArray = NDArray[np.float64]


def analyze_song(audio_path: Path, settings: SnippetSettings) -> SongAnalysis:
    """Tempo, beat times and downbeats for the whole song, in seconds from its start.

    librosa's tracked beats are quantized to frames, biased in tempo, and can skip the first
    bars, so they only seed a constant-tempo grid: the period comes from a line fit through the
    tracked beats, the phase from the actual onsets, and the grid spans all of the sound.
    """
    samples, rate = librosa.load(audio_path, sr=settings.sample_rate, mono=True)
    duration_s = len(samples) / rate
    onset_envelope = librosa.onset.onset_strength(y=samples, sr=rate, hop_length=HOP_LENGTH)
    _, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_envelope, sr=rate, hop_length=HOP_LENGTH
    )
    tracked_s = librosa.frames_to_time(beat_frames, sr=rate, hop_length=HOP_LENGTH)
    if len(tracked_s) < 2:
        raise ValueError(f"no steady beat found in {audio_path}")

    period_s, phase_s = _fit_constant_tempo(tracked_s)
    onsets_s = librosa.onset.onset_detect(
        onset_envelope=onset_envelope, sr=rate, hop_length=HOP_LENGTH, units="time", backtrack=True
    )
    phase_s += _median_offset_to_grid(onsets_s, period_s, phase_s)

    _, (sound_start, sound_end) = librosa.effects.trim(samples, top_db=SILENCE_TOP_DB)
    beats = _regular_grid(period_s, phase_s, sound_start / rate, sound_end / rate, duration_s)
    accents = _accent_strengths(onset_envelope, beats, period_s, rate)
    downbeat_phase = int(
        np.argmax(
            [
                accents[phase :: settings.beats_per_bar].mean()
                for phase in range(settings.beats_per_bar)
            ]
        )
    )
    beat_times = tuple(float(beat) for beat in beats)
    return SongAnalysis(
        duration_s=duration_s,
        bpm=60.0 / period_s,
        beats=beat_times,
        downbeats=beat_times[downbeat_phase :: settings.beats_per_bar],
    )


def _fit_constant_tempo(tracked_s: FloatArray) -> tuple[float, float]:
    """(period, time of the first tracked beat) from a least-squares line through the beats.

    Each gap is counted in whole beats first, so skipped beats don't bend the fit."""
    gaps_s = np.diff(tracked_s)
    rough_period_s = float(np.median(gaps_s))
    beat_numbers = np.concatenate([[0.0], np.cumsum(np.round(gaps_s / rough_period_s))])
    period_s, phase_s = np.polyfit(beat_numbers, tracked_s, 1)
    return float(period_s), float(phase_s)


def _median_offset_to_grid(onsets_s: FloatArray, period_s: float, phase_s: float) -> float:
    offsets_s = (onsets_s - phase_s + period_s / 2) % period_s - period_s / 2
    near_beats = offsets_s[np.abs(offsets_s) < period_s / 4]
    return float(np.median(near_beats)) if len(near_beats) else 0.0


def _regular_grid(
    period_s: float, phase_s: float, sound_start_s: float, sound_end_s: float, duration_s: float
) -> FloatArray:
    """Beats from the first sound to the last, allowing a quarter beat of slack at the start;
    the last beat must have at least a quarter beat of sound after it."""
    slack_s = period_s / 4
    first = int(np.ceil((sound_start_s - slack_s - phase_s) / period_s))
    last = int(np.floor((sound_end_s - slack_s - phase_s) / period_s))
    beats = phase_s + np.arange(first, last + 1, dtype=np.float64) * period_s
    return np.clip(beats, 0.0, duration_s)


def _accent_strengths(
    onset_envelope: FloatArray, beats: FloatArray, period_s: float, rate: float
) -> FloatArray:
    """Peak onset strength in the quarter beat that follows each beat."""
    frames = librosa.time_to_frames(beats, sr=rate, hop_length=HOP_LENGTH)
    width = max(1, int(period_s / 4 * rate / HOP_LENGTH))
    return np.array([onset_envelope[frame : frame + width].max() for frame in frames])


def detect_drop(
    audio_path: Path,
    song: SongAnalysis,
    start_s: float,
    end_s: float,
    settings: SnippetSettings,
) -> float | None:
    """The drop inside [start_s, end_s] as an absolute song time: the largest positive jump in
    the onset-strength/RMS envelope, snapped to the nearest downbeat. None if no clear drop.

    The jump at a frame is the ratio of mean RMS energy in the bar after it to the bar before it;
    a "clear" drop is at least MIN_DROP_JUMP_DB. The bars may reach outside the window.
    """
    samples, rate = librosa.load(audio_path, sr=settings.sample_rate, mono=True)
    energy = librosa.feature.rms(y=samples, hop_length=HOP_LENGTH)[0] ** 2
    bar_frames = round(settings.beats_per_bar * 60.0 / song.bpm * rate / HOP_LENGTH)
    frames = np.arange(bar_frames, len(energy) - bar_frames + 1)
    times_s = librosa.frames_to_time(frames, sr=rate, hop_length=HOP_LENGTH)
    in_window = (times_s >= start_s) & (times_s <= end_s)
    downbeats = [downbeat for downbeat in song.downbeats if start_s <= downbeat <= end_s]
    if not in_window.any() or not downbeats:
        return None

    cumulative = np.concatenate([[0.0], np.cumsum(energy)])
    before = cumulative[frames] - cumulative[frames - bar_frames]
    after = cumulative[frames + bar_frames] - cumulative[frames]
    jump_db = 10 * np.log10((after + ENERGY_FLOOR) / (before + ENERGY_FLOOR))
    jump_db[~in_window] = -np.inf
    best = int(np.argmax(jump_db))
    if jump_db[best] < MIN_DROP_JUMP_DB:
        return None
    return min(downbeats, key=lambda downbeat: abs(downbeat - times_s[best]))


def beat_grid_for_snippet(
    song: SongAnalysis, start_s: float, end_s: float, drop_s: float | None
) -> BeatGrid:
    """Pure: the song's beats inside [start_s, end_s], shifted to be relative to `start_s`.

    `drop_s` is an absolute song time (detected or owner override) or None.
    """
    if end_s <= start_s:
        raise ValueError(f"end_s ({end_s}) must be after start_s ({start_s})")
    if drop_s is not None and not start_s <= drop_s <= end_s:
        raise ValueError(f"drop_s ({drop_s}) must lie within [{start_s}, {end_s}]")

    def relative(times: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(time - start_s for time in times if start_s <= time <= end_s)

    return BeatGrid(
        bpm=song.bpm,
        beats=relative(song.beats),
        downbeats=relative(song.downbeats),
        drop_s=None if drop_s is None else drop_s - start_s,
        duration_s=end_s - start_s,
    )


def snap_start_to_downbeat(downbeats: tuple[float, ...], start_s: float, end_s: float) -> float:
    """Pure: the downbeat nearest `start_s` that is strictly before `end_s`, the earlier one on a
    tie, so a snippet (and its video) starts on a bar. Raises ValueError if there is none."""
    candidates = [downbeat for downbeat in downbeats if downbeat < end_s]
    if not candidates:
        raise ValueError(f"no downbeat before {end_s:.2f} s to start the snippet on")
    return min(candidates, key=lambda downbeat: (abs(downbeat - start_s), downbeat))

from pathlib import Path

from engine.config import SnippetSettings
from engine.snippets.models import BeatGrid, SongAnalysis


def analyze_song(audio_path: Path, settings: SnippetSettings) -> SongAnalysis:
    """Tempo, beat times and downbeats for the whole song, in seconds from its start."""
    raise NotImplementedError


def detect_drop(
    audio_path: Path,
    song: SongAnalysis,
    start_s: float,
    end_s: float,
    settings: SnippetSettings,
) -> float | None:
    """The drop inside [start_s, end_s] as an absolute song time: the largest positive jump in
    the onset-strength/RMS envelope, snapped to the nearest downbeat. None if no clear drop."""
    raise NotImplementedError


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

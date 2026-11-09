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
    raise NotImplementedError

from pathlib import Path

from engine.config import RenderSettings
from engine.recipes.models import Timeline


class RenderValidationError(Exception):
    """A rendered file doesn't match its plan (duration, 9:16 size, audio)."""


def render(
    timeline: Timeline,
    audio_path: Path,
    audio_start_s: float,
    out_path: Path,
    settings: RenderSettings,
) -> list[str]:
    """Render `timeline` to `out_path` with one FFmpeg filter graph (DESIGN.md §7.4).

    The audio is `timeline.duration_s` of `audio_path` starting at `audio_start_s` (the
    snippet's start in the song). Returns the exact ffmpeg argv, to store with the render.
    Raises engine.ffmpeg.FfmpegError if ffmpeg fails.
    """
    raise NotImplementedError


def validate(path: Path, expected_duration_s: float, settings: RenderSettings) -> None:
    """Check with ffprobe: duration within one frame of `expected_duration_s`,
    `settings.width` x `settings.height`, and an audio stream. Raises RenderValidationError."""
    raise NotImplementedError

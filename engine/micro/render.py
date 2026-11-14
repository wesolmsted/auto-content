from pathlib import Path

from engine.config import Settings
from engine.micro.models import MicroEdit, MicroSourceMedia


class MicroRenderValidationError(Exception):
    """A rendered micro file doesn't match its edit (duration, 9:16 size, audio)."""


def render_micro(
    edit: MicroEdit, source: MicroSourceMedia, out_path: Path, settings: Settings
) -> list[str]:
    """Render `source` with `edit` applied to `out_path`, then validate it with ffprobe.

    Returns the exact ffmpeg argv. Raises engine.ffmpeg.FfmpegError if ffmpeg fails and
    MicroRenderValidationError if the output doesn't match the plan.
    """
    raise NotImplementedError

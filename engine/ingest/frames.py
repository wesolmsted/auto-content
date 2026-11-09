"""Still frames from a shot: the tagger's sample frames and the thumbnail."""

import math
from pathlib import Path

from engine.ffmpeg import run_ffmpeg
from engine.ingest.scenes import ShotSpan


def sample_frame_indices(span: ShotSpan, count: int) -> list[int]:
    """`count` frames spread evenly from the first to the last frame of `span` (1 = the middle)."""
    if count == 1:
        return [span.start_frame + span.frame_count // 2]
    last = span.end_frame - 1
    step = (last - span.start_frame) / (count - 1)
    return [span.start_frame + int(index * step + 0.5) for index in range(count)]


def frame_time_s(frame: int, fps: float) -> float:
    """Seek time for `frame`, floored to the millisecond so ffmpeg never skips past the frame."""
    return math.floor(frame / fps * 1000) / 1000


def downscale_filter(width: int, height: int, max_long_side: int) -> str:
    """ffmpeg filter that shrinks (never enlarges) a frame so its long side fits, even sides."""
    scale = min(1.0, max_long_side / max(width, height))
    return f"scale={int(width * scale) // 2 * 2}:{int(height * scale) // 2 * 2}"


def extract_frame(
    video_path: Path,
    frame: int,
    fps: float,
    out_path: Path,
    video_filter: str,
    ffmpeg: str = "ffmpeg",
) -> Path:
    """Write one JPEG of `frame` (display orientation) through `video_filter`."""
    run_ffmpeg(
        [
            *["-ss", f"{frame_time_s(frame, fps):.3f}", "-i", str(video_path)],
            *["-frames:v", "1", "-vf", video_filter, "-q:v", "3", str(out_path)],
        ],
        ffmpeg=ffmpeg,
    )
    return out_path

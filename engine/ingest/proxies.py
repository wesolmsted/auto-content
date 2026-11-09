"""9:16-ready H.264 shot proxies (DESIGN.md §7.1 step 6). Renders read these, not the originals."""

from pathlib import Path

from engine.ffmpeg import run_ffmpeg
from engine.ingest.frames import frame_time_s
from engine.ingest.scenes import ShotSpan

PROXY_CRF = 18
PROXY_PRESET = "veryfast"


def _even(value: float) -> int:
    return int(value) // 2 * 2


def vertical_crop(width: int, height: int) -> tuple[int, int]:
    """The largest centered 9:16 box (even sides) that fits in `width` x `height`."""
    if width * 16 >= height * 9:
        return _even(height * 9 / 16), _even(height)
    return _even(width), _even(width * 16 / 9)


def fit_width(width: int, height: int, max_width: int) -> tuple[int, int]:
    """Scale down (never up) so the width is at most `max_width`, keeping even sides."""
    if width <= max_width:
        return _even(width), _even(height)
    return _even(max_width), _even(height * max_width / width)


def vertical_filter(width: int, height: int, max_width: int) -> str:
    """ffmpeg filter: center-crop a `width` x `height` display frame to 9:16, then fit the width."""
    crop_width, crop_height = vertical_crop(width, height)
    out_width, out_height = fit_width(crop_width, crop_height, max_width)
    return f"crop={crop_width}:{crop_height},scale={out_width}:{out_height},setsar=1"


def encode_proxy(
    video_path: Path,
    span: ShotSpan,
    fps: float,
    out_path: Path,
    video_filter: str,
    ffmpeg: str = "ffmpeg",
) -> Path:
    """Encode exactly the frames of `span`, silent, with a 1 s GOP so renders seek cheaply."""
    run_ffmpeg(
        [
            *["-ss", f"{frame_time_s(span.start_frame, fps):.3f}", "-i", str(video_path)],
            *["-frames:v", str(span.frame_count), "-vf", video_filter, "-an"],
            *["-c:v", "libx264", "-preset", PROXY_PRESET, "-crf", str(PROXY_CRF)],
            *["-pix_fmt", "yuv420p", "-g", str(round(fps)), "-movflags", "+faststart"],
            str(out_path),
        ],
        ffmpeg=ffmpeg,
    )
    return out_path

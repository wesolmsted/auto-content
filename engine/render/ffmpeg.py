from pathlib import Path

from engine.config import RenderSettings
from engine.ffmpeg import probe, run_ffmpeg
from engine.recipes.models import Timeline
from engine.render.filters import VIDEO_OUTPUT_LABEL, TextRenderStyle, build_filter_graph


class RenderValidationError(Exception):
    """A rendered file doesn't match its plan (duration, 9:16 size, audio)."""


def build_render_args(
    timeline: Timeline,
    audio_path: Path,
    audio_start_s: float,
    out_path: Path,
    settings: RenderSettings,
    text_style: TextRenderStyle | None = None,
) -> list[str]:
    """The ffmpeg arguments (without the binary) for `render`. Pure."""
    duration = str(timeline.duration_s)
    args: list[str] = []
    for segment in timeline.segments:
        args += ["-i", str(segment.source_path)]
    args += ["-ss", str(audio_start_s), "-t", duration, "-i", str(audio_path)]
    args += ["-filter_complex", build_filter_graph(timeline, settings, text_style)]
    args += ["-map", f"[{VIDEO_OUTPUT_LABEL}]", "-map", f"{len(timeline.segments)}:a"]
    args += [
        *["-c:v", settings.video_codec, "-profile:v", settings.video_profile],
        *["-crf", str(settings.crf), "-preset", settings.preset, "-pix_fmt", "yuv420p"],
        *["-r", str(settings.fps)],
        *["-c:a", settings.audio_codec, "-b:a", settings.audio_bitrate],
        *["-movflags", "+faststart"],
        *["-t", duration, str(out_path)],
    ]
    return args


def render(
    timeline: Timeline,
    audio_path: Path,
    audio_start_s: float,
    out_path: Path,
    settings: RenderSettings,
    *,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Render `timeline` to `out_path` with one FFmpeg filter graph (DESIGN.md §7.4).

    The audio is `timeline.duration_s` of `audio_path` starting at `audio_start_s` (the
    snippet's start in the song). Returns the exact ffmpeg argv, to store with the render.
    Raises engine.ffmpeg.FfmpegError if ffmpeg fails.
    """
    args = build_render_args(timeline, audio_path, audio_start_s, out_path, settings)
    completed = run_ffmpeg(args, ffmpeg=ffmpeg)
    return list(completed.args)


def validate(
    path: Path, expected_duration_s: float, settings: RenderSettings, *, ffprobe: str = "ffprobe"
) -> None:
    """Check with ffprobe: duration within one frame of `expected_duration_s`,
    `settings.width` x `settings.height`, and an audio stream. Raises RenderValidationError."""
    info = probe(path, ffprobe=ffprobe)
    problems = []
    if abs(info.duration_s - expected_duration_s) > 1 / settings.fps:
        problems.append(f"duration {info.duration_s:.3f}s, expected {expected_duration_s:.3f}s")
    if (info.width, info.height) != (settings.width, settings.height):
        problems.append(
            f"frame {info.width}x{info.height}, expected {settings.width}x{settings.height}"
        )
    if not info.has_audio:
        problems.append("no audio stream")
    if problems:
        raise RenderValidationError(f"{path.name}: " + "; ".join(problems))

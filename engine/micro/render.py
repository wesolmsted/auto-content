"""Render one micro variant with a single FFmpeg run (DESIGN.md §7.10)."""

import textwrap
from pathlib import Path

from engine.config import RenderSettings, Settings
from engine.ffmpeg import probe, run_ffmpeg
from engine.micro.edits import output_duration_s
from engine.micro.models import MicroEdit, MicroSourceMedia, MicroTextPosition
from engine.render.filters import TextRenderStyle, escape_filter_value

VIDEO_OUTPUT_LABEL = "video"
AUDIO_OUTPUT_LABEL = "audio"
AUDIO_SAMPLE_RATE = 48000
# Clear of Instagram's top UI (the Reels header and the account row).
TOP_TEXT_Y_FRACTION = 0.15
LINE_SPACING = 1.2


class MicroRenderValidationError(Exception):
    """A rendered micro file doesn't match its edit (duration, 9:16 size, audio)."""


def build_micro_args(
    edit: MicroEdit, source: MicroSourceMedia, out_path: Path, settings: Settings
) -> list[str]:
    """The ffmpeg arguments (without the binary) for `render_micro`. Pure."""
    duration = output_duration_s(edit, source.duration_s)
    if duration <= 0:
        raise ValueError(
            f"trims of {edit.trim_start_s}s and {edit.trim_end_s}s leave nothing left of "
            f"a {source.duration_s}s source"
        )
    render = settings.render
    trim_end_s = source.duration_s - edit.trim_end_s
    chains = [_video_chain(edit, trim_end_s, render, settings.micro.text_max_chars)]
    maps = ["-map", f"[{VIDEO_OUTPUT_LABEL}]"]
    audio_codec: list[str] = []
    if source.has_audio:
        chains.append(_audio_chain(edit, trim_end_s))
        maps += ["-map", f"[{AUDIO_OUTPUT_LABEL}]"]
        audio_codec = ["-c:a", render.audio_codec, "-b:a", render.audio_bitrate]
    return [
        *["-i", str(source.path)],
        *["-filter_complex", ";".join(chains)],
        *maps,
        *["-c:v", render.video_codec, "-profile:v", render.video_profile],
        *["-crf", str(render.crf), "-preset", render.preset],
        *["-pix_fmt", "yuv420p", "-r", str(render.fps)],
        *audio_codec,
        *["-map_metadata", "-1", "-movflags", "+faststart"],
        *["-t", _number(duration), str(out_path)],
    ]


def render_micro(
    edit: MicroEdit, source: MicroSourceMedia, out_path: Path, settings: Settings
) -> list[str]:
    """Render `source` with `edit` applied to `out_path`, then validate it with ffprobe.

    Returns the exact ffmpeg argv. Raises engine.ffmpeg.FfmpegError if ffmpeg fails and
    MicroRenderValidationError if the output doesn't match the plan.
    """
    args = build_micro_args(edit, source, out_path, settings)
    completed = run_ffmpeg(args, ffmpeg=settings.tools.ffmpeg)
    validate_micro(out_path, output_duration_s(edit, source.duration_s), source.has_audio, settings)
    return list(completed.args)


def validate_micro(
    path: Path, expected_duration_s: float, expect_audio: bool, settings: Settings
) -> None:
    """Check with ffprobe: duration within one frame of `expected_duration_s`, exactly the
    render size, and an audio stream iff `expect_audio`. Raises MicroRenderValidationError."""
    render = settings.render
    info = probe(path, ffprobe=settings.tools.ffprobe)
    problems = []
    if abs(info.duration_s - expected_duration_s) > 1 / render.fps:
        problems.append(f"duration {info.duration_s:.3f}s, expected {expected_duration_s:.3f}s")
    if (info.width, info.height) != (render.width, render.height):
        problems.append(
            f"frame {info.width}x{info.height}, expected {render.width}x{render.height}"
        )
    if expect_audio and not info.has_audio:
        problems.append("no audio stream")
    if info.has_audio and not expect_audio:
        problems.append("audio stream, expected none")
    if problems:
        raise MicroRenderValidationError(f"{path.name}: " + "; ".join(problems))


def _video_chain(
    edit: MicroEdit, trim_end_s: float, render: RenderSettings, text_max_chars: int
) -> str:
    filters = [
        f"trim=start={_number(edit.trim_start_s)}:end={_number(trim_end_s)}",
        f"setpts=(PTS-STARTPTS)/{_number(edit.speed)}",
        f"scale={_even(render.width * edit.zoom)}:{_even(render.height * edit.zoom)}"
        ":force_original_aspect_ratio=increase",
        f"crop=w={render.width}:h={render.height}"
        f":x=(iw-ow)*{_number(edit.crop_x)}:y=(ih-oh)*{_number(edit.crop_y)}",
        "setsar=1",
    ]
    if edit.mirror:
        filters.append("hflip")
    filters += [
        f"eq=brightness={_number(edit.brightness)}:contrast={_number(edit.contrast)}"
        f":saturation={_number(edit.saturation)}",
        f"hue=h={_number(edit.hue_degrees)}",
        *_text_filters(edit, render, text_max_chars),
        f"fps={render.fps}",
        "format=yuv420p",
    ]
    return "[0:v]" + ",".join(filters) + f"[{VIDEO_OUTPUT_LABEL}]"


def _audio_chain(edit: MicroEdit, trim_end_s: float) -> str:
    """Resampling at a scaled rate plays the audio faster or slower, so pitch moves with speed."""
    filters = [
        f"atrim=start={_number(edit.trim_start_s)}:end={_number(trim_end_s)}",
        "asetpts=PTS-STARTPTS",
        f"aresample={AUDIO_SAMPLE_RATE}",
        f"asetrate={round(AUDIO_SAMPLE_RATE * edit.speed)}",
        f"aresample={AUDIO_SAMPLE_RATE}",
    ]
    return "[0:a]" + ",".join(filters) + f"[{AUDIO_OUTPUT_LABEL}]"


def _text_filters(edit: MicroEdit, render: RenderSettings, text_max_chars: int) -> list[str]:
    style = TextRenderStyle.default(render)
    lines = textwrap.wrap(edit.text, width=text_max_chars) or [edit.text]
    font_size = round(edit.text_size_fraction * render.height)
    line_step = round(font_size * LINE_SPACING)
    block_height = (len(lines) - 1) * line_step + font_size
    if edit.text_position is MicroTextPosition.TOP:
        block_top = render.height * TOP_TEXT_Y_FRACTION
    else:
        block_top = (render.height - block_height) / 2
    first_y = round(block_top + edit.text_y_offset * render.height)
    return [
        "drawtext="
        + ":".join(
            [
                f"fontfile={escape_filter_value(str(style.font_file))}",
                f"text={escape_filter_value(line)}",
                "expansion=none",
                f"fontsize={font_size}",
                f"fontcolor={escape_filter_value(style.color)}",
                f"borderw={style.border_px}",
                f"bordercolor={escape_filter_value(style.border_color)}",
                "x=(w-text_w)/2",
                f"y={first_y + index * line_step}",
            ]
        )
        for index, line in enumerate(lines)
    ]


def _even(value: float) -> int:
    return 2 * round(value / 2)


def _number(value: float) -> str:
    return str(round(value, 6))

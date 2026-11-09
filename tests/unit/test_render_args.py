from pathlib import Path

from engine.config import load_settings
from engine.recipes.models import Look, Segment, Timeline
from engine.render.ffmpeg import build_render_args
from engine.render.filters import build_filter_graph

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = load_settings(REPO_ROOT / "config", env_file=None).render

TIMELINE = Timeline(
    duration_s=3.0,
    look=Look.NATURAL,
    segments=(
        Segment(
            shot_id=1,
            source_path=Path("/proxies/a.mp4"),
            source_in_s=0.0,
            source_out_s=1.0,
            output_start_s=0.0,
            output_end_s=1.0,
        ),
        Segment(
            shot_id=2,
            source_path=Path("/proxies/b.mp4"),
            source_in_s=4.0,
            source_out_s=6.0,
            output_start_s=1.0,
            output_end_s=3.0,
        ),
    ),
)


def build() -> list[str]:
    return build_render_args(
        TIMELINE, Path("/songs/song.wav"), 42.5, Path("/renders/out.mp4"), SETTINGS
    )


def option_values(args: list[str], option: str) -> list[str]:
    return [args[index + 1] for index, arg in enumerate(args) if arg == option]


def test_inputs_are_the_segment_proxies_then_the_snippet_audio_window() -> None:
    args = build()

    assert option_values(args, "-i") == ["/proxies/a.mp4", "/proxies/b.mp4", "/songs/song.wav"]
    audio_input = args.index("/songs/song.wav")
    assert args[audio_input - 5 : audio_input - 1] == ["-ss", "42.5", "-t", "3.0"]


def test_the_filter_graph_video_and_the_audio_input_are_mapped() -> None:
    args = build()

    assert option_values(args, "-filter_complex") == [build_filter_graph(TIMELINE, SETTINGS)]
    assert option_values(args, "-map") == ["[video]", "2:a"]


def test_encodes_h264_high_and_aac_at_the_output_rate_for_the_timeline_duration() -> None:
    args = build()

    assert option_values(args, "-c:v") == ["libx264"]
    assert option_values(args, "-profile:v") == ["high"]
    assert option_values(args, "-crf") == ["20"]
    assert option_values(args, "-preset") == ["medium"]
    assert option_values(args, "-pix_fmt") == ["yuv420p"]
    assert option_values(args, "-r") == ["30"]
    assert option_values(args, "-c:a") == ["aac"]
    assert option_values(args, "-b:a") == ["192k"]
    assert option_values(args, "-movflags") == ["+faststart"]
    assert args[-3:] == ["-t", "3.0", "/renders/out.mp4"]

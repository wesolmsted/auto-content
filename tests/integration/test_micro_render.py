import shutil
import subprocess
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from engine.config import Settings, load_settings
from engine.ffmpeg import probe
from engine.micro.edits import output_duration_s
from engine.micro.models import MicroEdit, MicroSourceMedia, MicroTextPosition
from engine.micro.render import MicroRenderValidationError, render_micro, validate_micro

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_FONT = REPO_ROOT / "engine" / "render" / "fonts" / "Anton-Regular.ttf"
# The Homebrew `ffmpeg` formula has no drawtext; `ffmpeg-full` (keg-only) does.
FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin")

Frame = npt.NDArray[np.uint8]


def has_drawtext(ffmpeg: str) -> bool:
    filters = subprocess.run(
        [ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, check=True
    )
    return " drawtext " in filters.stdout


def drawtext_tools() -> tuple[str, str] | None:
    """An (ffmpeg, ffprobe) pair whose ffmpeg can draw text, or None."""
    if has_drawtext("ffmpeg"):
        return "ffmpeg", "ffprobe"
    if (FFMPEG_FULL / "ffmpeg").exists() and has_drawtext(str(FFMPEG_FULL / "ffmpeg")):
        return str(FFMPEG_FULL / "ffmpeg"), str(FFMPEG_FULL / "ffprobe")
    return None


TOOLS = drawtext_tools()
needs_drawtext = pytest.mark.skipif(TOOLS is None, reason="no ffmpeg build with drawtext")


def make_settings(ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> Settings:
    settings = load_settings(REPO_ROOT / "config", env_file=None)
    return settings.model_copy(
        update={
            "render": settings.render.model_copy(
                update={"preset": "ultrafast", "font_file": BUNDLED_FONT}
            ),
            "tools": settings.tools.model_copy(update={"ffmpeg": ffmpeg, "ffprobe": ffprobe}),
        }
    )


@pytest.fixture
def settings() -> Settings:
    assert TOOLS is not None
    return make_settings(*TOOLS)


def make_edit(**changes: object) -> MicroEdit:
    values: dict[str, object] = {
        "trim_start_s": 0.3,
        "trim_end_s": 0.4,
        "speed": 1.03,
        "zoom": 1.05,
        "crop_x": 0.8,
        "crop_y": 0.2,
        "mirror": False,
        "brightness": 0.02,
        "contrast": 1.05,
        "saturation": 1.1,
        "hue_degrees": 3.0,
        "text": "POV: you finally hit the drop",
        "text_position": MicroTextPosition.TOP,
        "text_size_fraction": 0.05,
        "text_y_offset": 0.0,
    }
    return MicroEdit.model_validate(values | changes)


def source_for(path: Path) -> MicroSourceMedia:
    info = probe(path)
    return MicroSourceMedia(path=path, duration_s=info.duration_s, has_audio=info.has_audio)


def frame_at(path: Path, time_s: float, settings: Settings) -> Frame:
    completed = subprocess.run(
        [
            *[settings.tools.ffmpeg, "-v", "error", "-ss", str(time_s), "-i", str(path)],
            *["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        ],
        capture_output=True,
        check=True,
    )
    pixels = np.frombuffer(completed.stdout, dtype=np.uint8)
    return pixels.reshape(settings.render.height, settings.render.width, 3)


def mean_difference(first: Frame, second: Frame) -> float:
    return float(np.abs(first.astype(np.int16) - second.astype(np.int16)).mean())


@needs_drawtext
def test_landscape_source_with_audio_renders_to_the_planned_duration_with_audio(
    tmp_path: Path, settings: Settings, landscape_motion_clip: Path
) -> None:
    source = source_for(landscape_motion_clip)
    edit = make_edit()
    out = tmp_path / "micro_1.mp4"

    argv = render_micro(edit, source, out, settings)

    info = probe(out)
    planned = output_duration_s(edit, source.duration_s)
    assert planned == pytest.approx((6.0 - 0.7) / 1.03, abs=0.05)
    assert info.duration_s == pytest.approx(planned, abs=1 / 30)
    assert (info.width, info.height) == (1080, 1920)
    assert info.fps == pytest.approx(30.0)
    assert info.has_audio is True
    assert argv[0] == settings.tools.ffmpeg
    assert argv[-1] == str(out)


@needs_drawtext
def test_audio_is_resampled_so_it_ends_with_the_video(
    tmp_path: Path, settings: Settings, landscape_motion_clip: Path
) -> None:
    source = source_for(landscape_motion_clip)
    edit = make_edit(speed=0.97)
    out = tmp_path / "micro_slow.mp4"

    render_micro(edit, source, out, settings)

    audio_duration = subprocess.run(
        [
            *[settings.tools.ffprobe, "-v", "error", "-select_streams", "a:0"],
            *["-show_entries", "stream=duration,sample_rate", "-of", "csv=p=0", str(out)],
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    sample_rate, duration = audio_duration.split(",")
    assert sample_rate == "48000"
    assert float(duration) == pytest.approx((6.0 - 0.7) / 0.97, abs=0.05)


@needs_drawtext
def test_silent_portrait_source_renders_without_an_audio_stream(
    tmp_path: Path, settings: Settings, portrait_scenes_clip: Path
) -> None:
    source = source_for(portrait_scenes_clip)
    assert source.has_audio is False
    edit = make_edit(speed=0.98, text_position=MicroTextPosition.CENTER, mirror=True)
    out = tmp_path / "micro_2.mp4"

    render_micro(edit, source, out, settings)

    info = probe(out)
    assert info.duration_s == pytest.approx((10.0 - 0.7) / 0.98, abs=1 / 30)
    assert (info.width, info.height) == (1080, 1920)
    assert info.has_audio is False


@needs_drawtext
def test_mirrored_render_is_the_horizontal_flip_of_the_unmirrored_one(
    tmp_path: Path, settings: Settings, landscape_motion_clip: Path
) -> None:
    source = source_for(landscape_motion_clip)
    plain_out, mirrored_out = tmp_path / "plain.mp4", tmp_path / "mirrored.mp4"
    render_micro(make_edit(mirror=False), source, plain_out, settings)
    render_micro(make_edit(mirror=True), source, mirrored_out, settings)

    # The text is drawn after the flip so it stays readable; compare below it.
    below_text = slice(960, 1920)
    plain = frame_at(plain_out, 2.0, settings)[below_text]
    mirrored = frame_at(mirrored_out, 2.0, settings)[below_text]

    assert mean_difference(mirrored, np.fliplr(plain)) < 3
    assert mean_difference(mirrored, plain) > 20


@needs_drawtext
def test_a_render_that_misses_the_plan_raises_validation_error(
    tmp_path: Path, settings: Settings, portrait_scenes_clip: Path
) -> None:
    stale = MicroSourceMedia(path=portrait_scenes_clip, duration_s=14.0, has_audio=False)

    with pytest.raises(MicroRenderValidationError, match=r"duration 9\.\d+s, expected 12\.\d+s"):
        render_micro(make_edit(speed=1.03), stale, tmp_path / "micro_3.mp4", settings)


def test_validation_lists_every_mismatch(landscape_motion_clip: Path) -> None:
    with pytest.raises(MicroRenderValidationError) as raised:
        validate_micro(landscape_motion_clip, 4.0, False, make_settings())

    message = str(raised.value)
    assert "duration 6.0" in message
    assert "expected 4.000s" in message
    assert "frame 1280x720, expected 1080x1920" in message
    assert "audio stream, expected none" in message


def test_validation_requires_audio_when_the_source_has_it(
    tmp_path: Path, portrait_scenes_clip: Path
) -> None:
    copy = tmp_path / "portrait.mp4"
    shutil.copy(portrait_scenes_clip, copy)

    with pytest.raises(MicroRenderValidationError, match=r"^portrait\.mp4: no audio stream$"):
        validate_micro(copy, 10.0, True, make_settings())


def test_validation_passes_a_matching_file(portrait_scenes_clip: Path) -> None:
    validate_micro(portrait_scenes_clip, 10.0 + 1 / 60, False, make_settings())

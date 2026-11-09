import shutil
import subprocess
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from engine.config import RenderSettings, load_settings
from engine.ffmpeg import probe, run_ffmpeg
from engine.recipes.models import (
    Effect,
    EffectEvent,
    Look,
    Segment,
    TextEvent,
    Timeline,
)
from engine.render.ffmpeg import RenderValidationError, render, validate

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_FONT = REPO_ROOT / "engine" / "render" / "fonts" / "Anton-Regular.ttf"
SNIPPET_START_S = 8.0

Frame = npt.NDArray[np.uint8]


@pytest.fixture
def settings() -> RenderSettings:
    return load_settings(REPO_ROOT / "config", env_file=None).render.model_copy(
        update={
            "preset": "ultrafast",
            "luts_dir": REPO_ROOT / "config" / "luts",
            "font_file": BUNDLED_FONT,
        }
    )


def two_shot_timeline(
    portrait: Path,
    landscape: Path,
    look: Look = Look.NATURAL,
    effects: tuple[EffectEvent, ...] = (),
    texts: tuple[TextEvent, ...] = (),
) -> Timeline:
    return Timeline(
        duration_s=2.0,
        look=look,
        segments=(
            Segment(
                shot_id=1,
                source_path=portrait,
                source_in_s=1.0,
                source_out_s=2.0,
                output_start_s=0.0,
                output_end_s=1.0,
            ),
            Segment(
                shot_id=2,
                source_path=landscape,
                source_in_s=2.0,
                source_out_s=3.0,
                output_start_s=1.0,
                output_end_s=2.0,
            ),
        ),
        effects=effects,
        texts=texts,
    )


def frame_at(path: Path, time_s: float, settings: RenderSettings) -> Frame:
    completed = subprocess.run(
        [
            *["ffmpeg", "-v", "error", "-ss", str(time_s), "-i", str(path)],
            *["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        ],
        capture_output=True,
        check=True,
    )
    pixels = np.frombuffer(completed.stdout, dtype=np.uint8)
    return pixels.reshape(settings.height, settings.width, 3)


def max_channel_spread(frame: Frame) -> int:
    channels = frame.astype(np.int16)
    return int((channels.max(axis=2) - channels.min(axis=2)).max())


def has_drawtext() -> bool:
    filters = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=True
    )
    return " drawtext " in filters.stdout


def test_render_is_a_valid_portrait_video_with_the_snippet_audio(
    tmp_path: Path,
    settings: RenderSettings,
    portrait_scenes_clip: Path,
    landscape_motion_clip: Path,
    click_track_wav: Path,
) -> None:
    out = tmp_path / "render.mp4"
    timeline = two_shot_timeline(portrait_scenes_clip, landscape_motion_clip)

    argv = render(timeline, click_track_wav, SNIPPET_START_S, out, settings)

    validate(out, 2.0, settings)
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert info.duration_s == pytest.approx(2.0, abs=1 / 30)
    assert info.fps == pytest.approx(30.0)
    assert info.has_audio is True
    assert argv[0] == "ffmpeg"
    assert str(out) == argv[-1]


def test_landscape_shots_are_cropped_to_fill_the_frame_without_letterbox(
    tmp_path: Path,
    settings: RenderSettings,
    portrait_scenes_clip: Path,
    landscape_motion_clip: Path,
    click_track_wav: Path,
) -> None:
    out = tmp_path / "render.mp4"
    render(
        two_shot_timeline(portrait_scenes_clip, landscape_motion_clip),
        click_track_wav,
        SNIPPET_START_S,
        out,
        settings,
    )

    frame = frame_at(out, 1.5, settings)
    for edge in (frame[:8], frame[-8:], frame[:, :8], frame[:, -8:]):
        near_black = (edge.max(axis=2) < 24).mean()
        assert near_black < 0.5


def test_bw_look_renders_grayscale_frames(
    tmp_path: Path,
    settings: RenderSettings,
    portrait_scenes_clip: Path,
    landscape_motion_clip: Path,
    click_track_wav: Path,
) -> None:
    natural, bw = tmp_path / "natural.mp4", tmp_path / "bw.mp4"
    for path, look in ((natural, Look.NATURAL), (bw, Look.BW)):
        timeline = two_shot_timeline(portrait_scenes_clip, landscape_motion_clip, look=look)
        render(timeline, click_track_wav, SNIPPET_START_S, path, settings)

    assert max_channel_spread(frame_at(natural, 1.5, settings)) > 100
    assert max_channel_spread(frame_at(bw, 0.5, settings)) <= 8
    assert max_channel_spread(frame_at(bw, 1.5, settings)) <= 8


def test_every_effect_renders_and_keeps_the_planned_duration(
    tmp_path: Path,
    settings: RenderSettings,
    portrait_scenes_clip: Path,
    landscape_motion_clip: Path,
    click_track_wav: Path,
) -> None:
    out = tmp_path / "effects.mp4"
    effects = (
        EffectEvent(effect=Effect.ZOOM_PUNCH_ON_DOWNBEAT, start_s=0.0, duration_s=0.3),
        EffectEvent(effect=Effect.SPEED_RAMP, start_s=0.2, duration_s=0.6),
        EffectEvent(effect=Effect.SHAKE, start_s=1.0, duration_s=0.4),
        EffectEvent(effect=Effect.FLASH, start_s=1.5, duration_s=0.2),
    )
    timeline = two_shot_timeline(
        portrait_scenes_clip, landscape_motion_clip, look=Look.WARM, effects=effects
    )

    render(timeline, click_track_wav, SNIPPET_START_S, out, settings)

    validate(out, 2.0, settings)
    assert frame_at(out, 1.5, settings).mean() > frame_at(out, 1.8, settings).mean() + 40


def test_lut_paths_with_filter_graph_special_characters_are_escaped(
    tmp_path: Path,
    settings: RenderSettings,
    portrait_scenes_clip: Path,
    landscape_motion_clip: Path,
    click_track_wav: Path,
) -> None:
    luts_dir = tmp_path / "it's, a: [weird] dir"
    luts_dir.mkdir()
    shutil.copy(REPO_ROOT / "config" / "luts" / "bw.cube", luts_dir / "bw.cube")
    out = tmp_path / "bw.mp4"
    timeline = two_shot_timeline(portrait_scenes_clip, landscape_motion_clip, look=Look.BW)

    render(
        timeline,
        click_track_wav,
        SNIPPET_START_S,
        out,
        settings.model_copy(update={"luts_dir": luts_dir}),
    )

    assert max_channel_spread(frame_at(out, 1.5, settings)) <= 8


@pytest.mark.skipif(not has_drawtext(), reason="this ffmpeg build has no drawtext filter")
def test_text_is_burned_in_during_its_window(
    tmp_path: Path,
    settings: RenderSettings,
    portrait_scenes_clip: Path,
    landscape_motion_clip: Path,
    click_track_wav: Path,
) -> None:
    plain, texted = tmp_path / "plain.mp4", tmp_path / "text.mp4"
    text = TextEvent(text="POV: it's 3am, 100%", start_s=0.0, end_s=1.0)
    for path, texts in ((plain, ()), (texted, (text,))):
        timeline = two_shot_timeline(portrait_scenes_clip, landscape_motion_clip, texts=texts)
        render(timeline, click_track_wav, SNIPPET_START_S, path, settings)

    def difference(time_s: float) -> float:
        with_text = frame_at(texted, time_s, settings).astype(np.int16)
        without = frame_at(plain, time_s, settings).astype(np.int16)
        return float(np.abs(with_text - without).mean())

    assert difference(0.5) > 1.0
    assert difference(1.5) < 0.5


def test_validate_rejects_a_duration_off_by_more_than_a_frame(
    tmp_path: Path,
    settings: RenderSettings,
    portrait_scenes_clip: Path,
    landscape_motion_clip: Path,
    click_track_wav: Path,
) -> None:
    out = tmp_path / "render.mp4"
    timeline = two_shot_timeline(portrait_scenes_clip, landscape_motion_clip)
    render(timeline, click_track_wav, SNIPPET_START_S, out, settings)

    validate(out, 2.0 + 0.9 / 30, settings)
    with pytest.raises(RenderValidationError, match="duration"):
        validate(out, 2.0 + 2 / 30, settings)


def test_validate_rejects_a_frame_that_is_not_1080x1920(
    settings: RenderSettings, landscape_motion_clip: Path
) -> None:
    with pytest.raises(RenderValidationError, match="1280x720"):
        validate(landscape_motion_clip, 6.0, settings)


def test_validate_rejects_a_video_without_audio(tmp_path: Path, settings: RenderSettings) -> None:
    silent = tmp_path / "silent.mp4"
    run_ffmpeg(
        [
            *["-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=1"],
            *["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(silent)],
        ]
    )

    with pytest.raises(RenderValidationError, match="audio"):
        validate(silent, 1.0, settings)

import random
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.config import MicroSettings, load_settings
from engine.micro.edits import output_duration_s, sample_edit
from engine.micro.models import MicroEdit, MicroSourceMedia, MicroTextPosition
from engine.micro.render import build_micro_args
from engine.render.filters import escape_filter_value

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = load_settings(REPO_ROOT / "config", env_file=None)
MICRO = SETTINGS.micro
TEXT = "POV: you finally hit the drop"
EPSILON = 1e-9


def ordered_pair(low: float, high: float) -> st.SearchStrategy[tuple[float, float]]:
    return st.tuples(st.floats(low, high), st.floats(low, high)).map(
        lambda pair: (min(pair), max(pair))
    )


@st.composite
def micro_settings(draw: st.DrawFn) -> MicroSettings:
    """Any valid ranges, with speed changes below 50% and zooms of at least 1."""
    return MICRO.model_copy(
        update={
            "trim_s": draw(ordered_pair(0, 2)),
            "speed_change": draw(ordered_pair(0, 0.5)),
            "zoom": draw(ordered_pair(1, 1.5)),
            "mirror_probability": draw(st.floats(0, 1)),
            "brightness": draw(st.floats(0, 0.5)),
            "contrast": draw(ordered_pair(0.5, 1.5)),
            "saturation": draw(ordered_pair(0, 2)),
            "hue_degrees": draw(st.floats(0, 30)),
            "text_size_fraction": draw(st.floats(0.01, 0.2)),
            "text_size_jitter": draw(st.floats(0, 0.9)),
            "text_y_jitter": draw(st.floats(0, 0.2)),
        }
    )


seeds = st.integers(0, 2**32)


def within(value: float, low: float, high: float) -> bool:
    return low - EPSILON <= value <= high + EPSILON


@given(micro_settings(), seeds)
def test_every_sampled_value_is_within_its_configured_range(
    settings: MicroSettings, seed: int
) -> None:
    edit = sample_edit(random.Random(seed), settings, TEXT)

    assert within(edit.trim_start_s, *settings.trim_s)
    assert within(edit.trim_end_s, *settings.trim_s)
    assert within(abs(edit.speed - 1), *settings.speed_change)
    assert within(edit.zoom, *settings.zoom)
    assert within(edit.crop_x, 0, 1)
    assert within(edit.crop_y, 0, 1)
    assert within(edit.brightness, -settings.brightness, settings.brightness)
    assert within(edit.contrast, *settings.contrast)
    assert within(edit.saturation, *settings.saturation)
    assert within(edit.hue_degrees, -settings.hue_degrees, settings.hue_degrees)
    size_margin = settings.text_size_fraction * settings.text_size_jitter
    assert within(
        edit.text_size_fraction,
        settings.text_size_fraction - size_margin,
        settings.text_size_fraction + size_margin,
    )
    assert within(edit.text_y_offset, -settings.text_y_jitter, settings.text_y_jitter)
    assert edit.text == TEXT


@given(micro_settings(), seeds)
def test_the_same_seed_gives_the_same_edit(settings: MicroSettings, seed: int) -> None:
    first = sample_edit(random.Random(seed), settings, TEXT)
    second = sample_edit(random.Random(seed), settings, TEXT)

    assert first == second


@given(seeds)
def test_mirror_probability_zero_never_mirrors_and_one_always_does(seed: int) -> None:
    never = MICRO.model_copy(update={"mirror_probability": 0.0})
    always = MICRO.model_copy(update={"mirror_probability": 1.0})

    assert sample_edit(random.Random(seed), never, TEXT).mirror is False
    assert sample_edit(random.Random(seed), always, TEXT).mirror is True


def test_degenerate_ranges_give_exact_values() -> None:
    settings = MICRO.model_copy(
        update={
            "trim_s": (0.2, 0.2),
            "speed_change": (0.02, 0.02),
            "zoom": (1.03, 1.03),
            "brightness": 0.0,
            "contrast": (1.05, 1.05),
            "saturation": (0.9, 0.9),
            "hue_degrees": 0.0,
            "text_size_fraction": 0.05,
            "text_size_jitter": 0.0,
            "text_y_jitter": 0.0,
        }
    )

    edits = [sample_edit(random.Random(seed), settings, TEXT) for seed in range(50)]

    for edit in edits:
        assert edit.trim_start_s == pytest.approx(0.2)
        assert edit.trim_end_s == pytest.approx(0.2)
        assert edit.zoom == pytest.approx(1.03)
        assert edit.brightness == 0
        assert edit.contrast == pytest.approx(1.05)
        assert edit.saturation == pytest.approx(0.9)
        assert edit.hue_degrees == 0
        assert edit.text_size_fraction == pytest.approx(0.05)
        assert edit.text_y_offset == 0
    assert {round(edit.speed, 9) for edit in edits} == {0.98, 1.02}
    assert {edit.text_position for edit in edits} == set(MicroTextPosition)
    assert {edit.mirror for edit in edits} == {True, False}


def test_default_ranges_vary_between_seeds() -> None:
    edits = [sample_edit(random.Random(seed), MICRO, TEXT) for seed in range(20)]

    assert len({edit.trim_start_s for edit in edits}) == 20
    assert len({edit.crop_x for edit in edits}) == 20
    assert len({edit.hue_degrees for edit in edits}) == 20


def make_edit(**changes: object) -> MicroEdit:
    values: dict[str, object] = {
        "trim_start_s": 0.2,
        "trim_end_s": 0.3,
        "speed": 1.02,
        "zoom": 1.04,
        "crop_x": 0.25,
        "crop_y": 0.75,
        "mirror": True,
        "brightness": 0.02,
        "contrast": 1.05,
        "saturation": 1.1,
        "hue_degrees": -3.0,
        "text": TEXT,
        "text_position": MicroTextPosition.TOP,
        "text_size_fraction": 0.05,
        "text_y_offset": 0.01,
    }
    return MicroEdit.model_validate(values | changes)


def test_output_duration_is_the_trimmed_source_divided_by_speed() -> None:
    assert output_duration_s(make_edit(), 10.0) == pytest.approx(9.5 / 1.02)
    assert output_duration_s(
        make_edit(trim_start_s=0.5, trim_end_s=0.5, speed=0.98), 6.0
    ) == pytest.approx(5.0 / 0.98)
    assert output_duration_s(make_edit(trim_start_s=0, trim_end_s=0, speed=1), 4.0) == 4.0


SOURCE = MicroSourceMedia(
    path=Path("/media/micro/beach_micro.mov"), duration_s=10.0, has_audio=True
)
FONT = "fontfile='engine/render/fonts/Anton-Regular.ttf'"
TEXT_STYLE = "expansion=none:fontsize=96:fontcolor='white':borderw=5:bordercolor='black'"


def test_args_apply_every_edit_in_one_graph_with_pitch_shifted_audio() -> None:
    args = build_micro_args(make_edit(), SOURCE, Path("/renders/micro_7.mp4"), SETTINGS)

    video = (
        "[0:v]trim=start=0.2:end=9.7,setpts=(PTS-STARTPTS)/1.02,"
        "crop=w=iw/1.04:h=ih/1.04:x=(iw-ow)*0.25:y=(ih-oh)*0.75,"
        "scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        "setsar=1,hflip,eq=brightness=0.02:contrast=1.05:saturation=1.1,hue=h=-3.0,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,"
        f"drawtext={FONT}:text='POV\\: you finally':{TEXT_STYLE}:x=(w-text_w)/2:y=307,"
        f"drawtext={FONT}:text='hit the drop':{TEXT_STYLE}:x=(w-text_w)/2:y=422,"
        "fps=30,format=yuv420p[video]"
    )
    audio = (
        "[0:a]atrim=start=0.2:end=9.7,asetpts=PTS-STARTPTS,"
        "aresample=48000,asetrate=48960,aresample=48000[audio]"
    )
    assert args == [
        *["-i", "/media/micro/beach_micro.mov"],
        *["-filter_complex", f"{video};{audio}"],
        *["-map", "[video]", "-map", "[audio]"],
        *["-c:v", "libx264", "-profile:v", "high", "-crf", "20", "-preset", "medium"],
        *["-pix_fmt", "yuv420p", "-r", "30"],
        *["-c:a", "aac", "-b:a", "192k"],
        *["-map_metadata", "-1", "-movflags", "+faststart"],
        *["-t", "9.313725", "/renders/micro_7.mp4"],
    ]


def test_a_source_without_audio_gets_no_audio_chain_or_codec() -> None:
    silent = SOURCE.model_copy(update={"has_audio": False})

    args = build_micro_args(make_edit(), silent, Path("/renders/micro_7.mp4"), SETTINGS)

    graph = args[args.index("-filter_complex") + 1]
    assert "[0:a]" not in graph
    assert "[audio]" not in args
    assert "-c:a" not in args
    assert args[args.index("-map") + 1] == "[video]"
    assert args.count("-map") == 1


def test_unmirrored_edit_has_no_hflip() -> None:
    args = build_micro_args(make_edit(mirror=False), SOURCE, Path("/r.mp4"), SETTINGS)

    graph = args[args.index("-filter_complex") + 1]
    assert "hflip" not in graph
    assert ",setsar=1,eq=brightness=" in graph


def test_centered_text_block_is_centered_vertically_and_shifted_by_the_offset() -> None:
    edit = make_edit(text_position=MicroTextPosition.CENTER, text_y_offset=-0.02)

    graph = build_micro_args(edit, SOURCE, Path("/r.mp4"), SETTINGS)[3]

    # Two lines: block height 115 + 96 = 211, top (1920 - 211) / 2 - 38.4 = 816.1.
    assert "text='POV\\: you finally'" in graph
    assert ":y=816," in graph
    assert ":y=931," in graph


def test_short_text_is_a_single_line_and_its_size_follows_the_edit() -> None:
    edit = make_edit(text="wait for it", text_size_fraction=0.06, text_y_offset=0.0)

    graph = build_micro_args(edit, SOURCE, Path("/r.mp4"), SETTINGS)[3]

    assert graph.count("drawtext=") == 1
    assert "text='wait for it'" in graph
    assert "fontsize=115:" in graph
    assert ":y=288," in graph


def test_long_text_wraps_at_text_max_chars() -> None:
    edit = make_edit(text="one two three four five six seven")
    settings = SETTINGS.model_copy(
        update={"micro": MICRO.model_copy(update={"text_max_chars": 10})}
    )

    graph = build_micro_args(edit, SOURCE, Path("/r.mp4"), settings)[3]

    lines = [part.split("'")[1] for part in graph.split("text=")[1:] if part.startswith("'")]
    assert lines == ["one two", "three four", "five six", "seven"]


def test_trims_that_leave_nothing_are_rejected() -> None:
    edit = make_edit(trim_start_s=2.0, trim_end_s=2.0)
    short = SOURCE.model_copy(update={"duration_s": 4.0})

    with pytest.raises(ValueError, match="nothing left"):
        build_micro_args(edit, short, Path("/r.mp4"), SETTINGS)


def test_text_cutoff_on_the_source_is_moved_by_the_trim_and_speed() -> None:
    edit = make_edit(text_cutoff_s=2.4)

    graph = build_micro_args(edit, SOURCE, Path("/r.mp4"), SETTINGS)[3]

    # (2.4 - 0.2) / 1.02 = 2.156863 s into the render; both text lines stop together.
    assert graph.count(":enable='lt(t,2.156863)'") == 2
    assert graph.count("drawtext=") == 2


def test_without_a_cutoff_the_text_stays_for_the_whole_render() -> None:
    graph = build_micro_args(make_edit(), SOURCE, Path("/r.mp4"), SETTINGS)[3]

    assert "enable=" not in graph
    assert graph.count("drawtext=") == 2


def test_a_cutoff_inside_the_trimmed_start_draws_no_text() -> None:
    edit = make_edit(trim_start_s=0.4, text_cutoff_s=0.3)

    graph = build_micro_args(edit, SOURCE, Path("/r.mp4"), SETTINGS)[3]

    assert "drawtext=" not in graph
    assert ",pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,fps=30," in graph


def test_micro_font_file_replaces_the_render_font_and_expands_home() -> None:
    micro = MicroSettings.model_validate(
        MICRO.model_dump() | {"font_file": "~/fonts/Helvetica-Bold.ttf"}
    )
    settings = SETTINGS.model_copy(update={"micro": micro})

    graph = build_micro_args(make_edit(), SOURCE, Path("/r.mp4"), settings)[3]

    font = escape_filter_value(str(Path.home() / "fonts" / "Helvetica-Bold.ttf"))
    assert graph.count(f"drawtext=fontfile={font}:") == 2
    assert "Anton" not in graph

import re
from pathlib import Path

import pytest

from engine.config import RenderSettings, load_settings
from engine.recipes.models import (
    Effect,
    EffectEvent,
    Look,
    Segment,
    TextCase,
    TextEvent,
    TextPosition,
    TextStyle,
    Timeline,
)
from engine.render.filters import (
    VIDEO_OUTPUT_LABEL,
    TextRenderStyle,
    build_filter_graph,
    escape_filter_value,
    lut_path_for,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings() -> RenderSettings:
    return load_settings(REPO_ROOT / "config", env_file=None).render.model_copy(
        update={"luts_dir": Path("/luts"), "font_file": Path("/fonts/text.ttf")}
    )


def make_timeline(
    look: Look = Look.NATURAL,
    effects: tuple[EffectEvent, ...] = (),
    texts: tuple[TextEvent, ...] = (),
) -> Timeline:
    return Timeline(
        duration_s=3.0,
        look=look,
        segments=(
            Segment(
                shot_id=1,
                source_path=Path("/proxies/a.mp4"),
                source_in_s=0.5,
                source_out_s=1.5,
                output_start_s=0.0,
                output_end_s=1.0,
            ),
            Segment(
                shot_id=2,
                source_path=Path("/proxies/b.mp4"),
                source_in_s=2.0,
                source_out_s=3.0,
                output_start_s=1.0,
                output_end_s=3.0,
                speed=0.5,
            ),
        ),
        effects=effects,
        texts=texts,
    )


def test_each_segment_is_trimmed_from_its_own_input_at_its_source_times(
    settings: RenderSettings,
) -> None:
    graph = build_filter_graph(make_timeline(), settings)

    assert re.findall(r"\[(\d+):v\]trim=start=([\d.]+):end=([\d.]+)", graph) == [
        ("0", "0.5", "1.5"),
        ("1", "2.0", "3.0"),
    ]


def test_slow_motion_segments_stretch_their_timestamps_by_one_over_speed(
    settings: RenderSettings,
) -> None:
    graph = build_filter_graph(make_timeline(), settings)

    assert re.findall(r"setpts=\(PTS-STARTPTS\)/([\d.]+)", graph) == ["1.0", "0.5"]


def test_every_segment_is_scaled_to_cover_then_center_cropped_to_the_output_size(
    settings: RenderSettings,
) -> None:
    graph = build_filter_graph(make_timeline(), settings)

    cover = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
    assert graph.count(cover) == 2
    assert graph.count("fps=30") >= 2


def test_segments_are_concatenated_in_order_into_the_output_label(
    settings: RenderSettings,
) -> None:
    graph = build_filter_graph(make_timeline(), settings)

    assert "[segment0][segment1]concat=n=2:v=1:a=0" in graph
    assert graph.endswith(f"format=yuv420p[{VIDEO_OUTPUT_LABEL}]")


def test_natural_look_has_no_lut_and_other_looks_load_their_cube_file(
    settings: RenderSettings,
) -> None:
    assert "lut3d" not in build_filter_graph(make_timeline(Look.NATURAL), settings)
    assert "lut3d=file='/luts/bw.cube'" in build_filter_graph(make_timeline(Look.BW), settings)
    assert "lut3d=file='/luts/warm.cube'" in build_filter_graph(make_timeline(Look.WARM), settings)


def test_lut_path_for_each_look() -> None:
    assert lut_path_for(Look.NATURAL, Path("/luts")) is None
    assert lut_path_for(Look.HIGH_CONTRAST, Path("/luts")) == Path("/luts/high_contrast.cube")


@pytest.mark.parametrize(
    ("value", "escaped"),
    [
        ("plain", "'plain'"),
        ("a:b", "'a\\:b'"),
        ("it's", "'it\\'\\''s'"),
        ("back\\slash", "'back\\\\slash'"),
        ("x, y; [z]", "'x, y; [z]'"),
    ],
)
def test_filter_values_are_escaped_for_both_option_and_graph_parsing(
    value: str, escaped: str
) -> None:
    assert escape_filter_value(value) == escaped


def test_text_is_drawn_with_the_font_for_its_time_window_and_case(
    settings: RenderSettings,
) -> None:
    text = TextEvent(
        text="POV: it's 3am",
        start_s=0.5,
        end_s=2.0,
        style=TextStyle(position=TextPosition.TOP, case=TextCase.LOWER),
    )
    graph = build_filter_graph(make_timeline(texts=(text,)), settings)

    assert graph.count("drawtext=") == 1
    drawtext = graph[graph.index("drawtext=") :]
    assert "fontfile='/fonts/text.ttf'" in drawtext
    assert "text='pov\\: it\\'\\''s 3am'" in drawtext
    assert "expansion=none" in drawtext
    assert "x=(w-text_w)/2" in drawtext
    assert "y=h*0.12" in drawtext
    assert "enable='between(t,0.5,2.0)'" in drawtext


@pytest.mark.parametrize(
    ("position", "case", "expected_text", "expected_y"),
    [
        (TextPosition.CENTER, TextCase.UPPER, "text='LOUD'", "y=(h-text_h)/2"),
        (TextPosition.LOWER_THIRD, TextCase.AS_WRITTEN, "text='Loud'", "y=h*0.70-text_h/2"),
    ],
)
def test_text_position_and_case_options(
    settings: RenderSettings,
    position: TextPosition,
    case: TextCase,
    expected_text: str,
    expected_y: str,
) -> None:
    text = TextEvent(text="Loud", start_s=0, end_s=1, style=TextStyle(position=position, case=case))
    graph = build_filter_graph(make_timeline(texts=(text,)), settings)

    assert expected_text in graph
    assert expected_y in graph


def test_a_timeline_without_effects_or_text_has_no_effect_filters(
    settings: RenderSettings,
) -> None:
    graph = build_filter_graph(make_timeline(), settings)

    for name in ("zoompan", "eq=", "drawtext", "sqrt", "sin("):
        assert name not in graph


def test_zoom_punches_are_keyed_to_their_events(settings: RenderSettings) -> None:
    effects = (
        EffectEvent(effect=Effect.ZOOM_PUNCH_ON_DOWNBEAT, start_s=1.0, duration_s=0.25),
        EffectEvent(effect=Effect.ZOOM_PUNCH_ON_DOWNBEAT, start_s=2.0, duration_s=0.25),
    )
    graph = build_filter_graph(make_timeline(effects=effects), settings)

    assert graph.count("zoompan=") == 1
    assert "between(it,1.0,1.25)" in graph
    assert "between(it,2.0,2.25)" in graph
    assert "s=1080x1920" in graph


def test_flash_brightens_only_within_its_event(settings: RenderSettings) -> None:
    effects = (EffectEvent(effect=Effect.FLASH, start_s=1.0, duration_s=0.2),)
    graph = build_filter_graph(make_timeline(effects=effects), settings)

    assert graph.count("eq=brightness=") == 1
    assert "enable='between(t,1.0,1.2)'" in graph


def test_shake_jitters_the_crop_within_its_event(settings: RenderSettings) -> None:
    effects = (EffectEvent(effect=Effect.SHAKE, start_s=0.5, duration_s=0.4),)
    graph = build_filter_graph(make_timeline(effects=effects), settings)

    crop = re.search(r"crop=w=iw-(\d+):h=ih-(\d+):x='([^']*)':y='([^']*)'", graph)
    assert crop is not None
    assert "between(t,0.5,0.9)" in crop.group(3)
    assert "between(t,0.5,0.9)" in crop.group(4)
    assert graph[crop.end() :].startswith(",scale=1080:1920")


def test_speed_ramp_retimes_its_window_and_keeps_the_frame_rate(
    settings: RenderSettings,
) -> None:
    effects = (EffectEvent(effect=Effect.SPEED_RAMP, start_s=1.0, duration_s=1.0),)
    graph = build_filter_graph(make_timeline(effects=effects), settings)

    assert "setpts='if(between(T,1.0,2.0),1.0+1.0*sqrt((T-1.0)/1.0),T)/TB',fps=30" in graph


def test_text_render_style_is_passed_in_rather_than_fixed(settings: RenderSettings) -> None:
    text = TextEvent(text="hi", start_s=0, end_s=1)
    style = TextRenderStyle(
        font_file=Path("/fonts/other.otf"),
        size_fraction=0.05,
        color="yellow",
        border_px=2,
        border_color="0x000000@0.5",
    )
    graph = build_filter_graph(make_timeline(texts=(text,)), settings, text_style=style)

    assert "fontfile='/fonts/other.otf'" in graph
    assert "fontsize=96" in graph
    assert "fontcolor='yellow'" in graph
    assert "borderw=2" in graph
    assert "bordercolor='0x000000@0.5'" in graph


def test_default_text_style_uses_the_configured_font(settings: RenderSettings) -> None:
    style = TextRenderStyle.default(settings)

    assert style.font_file == Path("/fonts/text.ttf")
    assert style.color == "white"

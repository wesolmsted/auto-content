"""Pure builder for the render's FFmpeg filter graph (DESIGN.md §7.4).

Input `i` of the graph is segment `i`'s proxy file. Each segment is trimmed, retimed by its
speed, scaled to cover the output frame and center-cropped. The segments are concatenated,
then the timeline-wide filters run in output time: speed ramps, zoom punches, shake, flash,
the look's LUT and the text overlays.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from engine.config import RenderSettings
from engine.recipes.models import (
    Effect,
    EffectEvent,
    Look,
    TextCase,
    TextEvent,
    TextPosition,
    Timeline,
)

VIDEO_OUTPUT_LABEL = "video"

ZOOM_PUNCH_AMOUNT = 0.15
FLASH_BRIGHTNESS = 0.6
SHAKE_MARGIN_FRACTION = 0.02

TEXT_Y_BY_POSITION = {
    TextPosition.TOP: "h*0.12",
    TextPosition.CENTER: "(h-text_h)/2",
    TextPosition.LOWER_THIRD: "h*0.70-text_h/2",
}


@dataclass(frozen=True)
class TextRenderStyle:
    """How burned-in text looks. `size_fraction` is the font size as a fraction of frame height."""

    font_file: Path
    size_fraction: float = 0.045
    color: str = "white"
    border_px: int = 5
    border_color: str = "black"

    @classmethod
    def default(cls, settings: RenderSettings) -> "TextRenderStyle":
        return cls(font_file=settings.font_file)


def escape_filter_value(value: str) -> str:
    """Quote `value` for a filter option inside a filter graph.

    FFmpeg unescapes twice: the graph parser strips one level of quoting, then the option
    parser treats `\\`, `'` and `:` as special. So escape for the option parser first, then
    single-quote the result for the graph parser.
    """
    option_escaped = "".join(f"\\{char}" if char in "\\':" else char for char in value)
    return "'" + option_escaped.replace("'", "'\\''") + "'"


def lut_path_for(look: Look, luts_dir: Path) -> Path | None:
    if look is Look.NATURAL:
        return None
    return luts_dir / f"{look.value}.cube"


def build_filter_graph(
    timeline: Timeline, settings: RenderSettings, text_style: TextRenderStyle | None = None
) -> str:
    size = f"{settings.width}:{settings.height}"
    chains = []
    for index, segment in enumerate(timeline.segments):
        chains.append(
            f"[{index}:v]"
            f"trim=start={_seconds(segment.source_in_s)}:end={_seconds(segment.source_out_s)},"
            f"setpts=(PTS-STARTPTS)/{_seconds(segment.speed)},"
            f"scale={size}:force_original_aspect_ratio=increase,crop={size},setsar=1,"
            f"fps={settings.fps},format=yuv420p"
            f"[segment{index}]"
        )
    concat_inputs = "".join(f"[segment{index}]" for index in range(len(timeline.segments)))
    filters = [f"{concat_inputs}concat=n={len(timeline.segments)}:v=1:a=0"]
    filters += _effect_filters(timeline.effects, settings)
    lut_path = lut_path_for(timeline.look, settings.luts_dir)
    if lut_path is not None:
        filters.append(f"lut3d=file={escape_filter_value(str(lut_path))}")
    text_style = text_style or TextRenderStyle.default(settings)
    filters += [_drawtext(text, text_style, settings) for text in timeline.texts]
    filters.append(f"format=yuv420p[{VIDEO_OUTPUT_LABEL}]")
    chains.append(",".join(filters))
    return ";".join(chains)


def _effect_filters(effects: Sequence[EffectEvent], settings: RenderSettings) -> list[str]:
    by_effect = {
        effect: [event for event in effects if event.effect is effect] for effect in Effect
    }
    filters = []
    ramps = by_effect[Effect.SPEED_RAMP]
    if ramps:
        filters += [_speed_ramp(event) for event in ramps]
        filters.append(f"fps={settings.fps}")
    if by_effect[Effect.ZOOM_PUNCH_ON_DOWNBEAT]:
        filters.append(_zoom_punch(by_effect[Effect.ZOOM_PUNCH_ON_DOWNBEAT], settings))
    if by_effect[Effect.SHAKE]:
        filters += _shake(by_effect[Effect.SHAKE], settings)
    filters += [_flash(event) for event in by_effect[Effect.FLASH]]
    return filters


def _speed_ramp(event: EffectEvent) -> str:
    """Remap the window so it plays slow, then fast, ending where it started to: output time
    a + d*sqrt((T-a)/d) for input time T. The total duration doesn't change."""
    start, duration = _seconds(event.start_s), _seconds(event.duration_s)
    end = _seconds(event.start_s + event.duration_s)
    return (
        f"setpts='if(between(T,{start},{end}),"
        f"{start}+{duration}*sqrt((T-{start})/{duration}),T)/TB'"
    )


def _zoom_punch(events: Sequence[EffectEvent], settings: RenderSettings) -> str:
    """Jump in by ZOOM_PUNCH_AMOUNT at each event's start and ease back out over its duration."""
    punches = "+".join(
        f"between(it,{_window(event)})*(1-(it-{_seconds(event.start_s)})/{_seconds(event.duration_s)})"
        for event in events
    )
    return (
        f"zoompan=z='1+{ZOOM_PUNCH_AMOUNT}*({punches})'"
        ":x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
        f":d=1:s={settings.width}x{settings.height}:fps={settings.fps}"
    )


def _shake(events: Sequence[EffectEvent], settings: RenderSettings) -> list[str]:
    """Crop a small margin off every frame, and wobble the crop window during the events."""
    margin = round(settings.width * SHAKE_MARGIN_FRACTION)
    active = "+".join(f"between(t,{_window(event)})" for event in events)
    return [
        f"crop=w=iw-{2 * margin}:h=ih-{2 * margin}"
        f":x='{margin}+{margin}*sin(t*90)*({active})'"
        f":y='{margin}+{margin}*cos(t*70)*({active})'",
        f"scale={settings.width}:{settings.height}",
    ]


def _flash(event: EffectEvent) -> str:
    return (
        f"eq=brightness='{FLASH_BRIGHTNESS}*(1-(t-{_seconds(event.start_s)})"
        f"/{_seconds(event.duration_s)})':eval=frame:enable='between(t,{_window(event)})'"
    )


def _drawtext(text: TextEvent, style: TextRenderStyle, settings: RenderSettings) -> str:
    options = [
        f"fontfile={escape_filter_value(str(style.font_file))}",
        f"text={escape_filter_value(_apply_case(text.text, text.style.case))}",
        "expansion=none",
        f"fontsize={round(settings.height * style.size_fraction)}",
        f"fontcolor={escape_filter_value(style.color)}",
        f"borderw={style.border_px}",
        f"bordercolor={escape_filter_value(style.border_color)}",
        "x=(w-text_w)/2",
        f"y={TEXT_Y_BY_POSITION[text.style.position]}",
        f"enable='between(t,{_seconds(text.start_s)},{_seconds(text.end_s)})'",
    ]
    return "drawtext=" + ":".join(options)


def _apply_case(text: str, case: TextCase) -> str:
    if case is TextCase.UPPER:
        return text.upper()
    if case is TextCase.LOWER:
        return text.lower()
    return text


def _window(event: EffectEvent) -> str:
    return f"{_seconds(event.start_s)},{_seconds(event.start_s + event.duration_s)}"


def _seconds(value: float) -> str:
    return str(round(value, 6))

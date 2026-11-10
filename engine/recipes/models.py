"""Recipe, Timeline and render plan models shared by assembly, render and hand-off."""

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from engine.db.enums import Orientation, Platform, SnippetType

TIME_TOLERANCE_S = 1e-6


class CutRate(StrEnum):
    EVERY_BEAT = "every_beat"
    EVERY_2_BEATS = "every_2_beats"
    DOWNBEATS = "downbeats"
    ONE_LONG_SHOT = "one_long_shot"


class OpeningShot(StrEnum):
    MOST_MOTION = "most_motion"
    PERSON_FACE = "person_face"
    SCENIC = "scenic"
    TEXT_LED = "text_led"


class FootageCategory(StrEnum):
    CITY_NIGHT = "city_night"
    NATURE = "nature"
    STUDIO_PERFORMANCE = "studio_performance"
    CROWD = "crowd"
    MIXED = "mixed"


class OnScreenText(StrEnum):
    NONE = "none"
    HOOK_LYRIC = "hook_lyric"
    POV_CAPTION = "pov_caption"


class Look(StrEnum):
    NATURAL = "natural"
    WARM = "warm"
    HIGH_CONTRAST = "high_contrast"
    BW = "bw"


class Effect(StrEnum):
    ZOOM_PUNCH_ON_DOWNBEAT = "zoom_punch_on_downbeat"
    SPEED_RAMP = "speed_ramp"
    FLASH = "flash"
    SHAKE = "shake"


class TextPosition(StrEnum):
    TOP = "top"
    CENTER = "center"
    LOWER_THIRD = "lower_third"


class TextCase(StrEnum):
    AS_WRITTEN = "as_written"
    LOWER = "lower"
    UPPER = "upper"


class TextStyle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    position: TextPosition = TextPosition.LOWER_THIRD
    case: TextCase = TextCase.LOWER


class Recipe(BaseModel):
    """One combination of style choices. `effects` is a set: an empty tuple means no effects.

    `id` is derived from the canonical params, so the same choices always give the same id.
    `source` is provenance only and is not part of the id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cut_rate: CutRate
    hard_cut_on_drop: bool = True
    opening_shot: OpeningShot
    footage_category: FootageCategory
    footage_orientation: Orientation
    length_s: int = Field(gt=0)
    on_screen_text: OnScreenText
    text_style: TextStyle = TextStyle()
    snippet_type: SnippetType
    look: Look
    effects: tuple[Effect, ...] = ()
    source: str = "generated"

    @field_validator("effects")
    @classmethod
    def _effects_as_sorted_set(cls, effects: tuple[Effect, ...]) -> tuple[Effect, ...]:
        return tuple(sorted(set(effects)))

    def canonical_params(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"source"})

    @property
    def id(self) -> str:
        canonical = json.dumps(self.canonical_params(), sort_keys=True, separators=(",", ":"))
        return "rcp_" + hashlib.sha256(canonical.encode()).hexdigest()[:12]


class Segment(BaseModel):
    """One shot's span on the output timeline. Source times are within the shot's proxy file.

    Source duration equals output duration times `speed` (speed < 1 is slow motion).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    shot_id: int
    source_path: Path
    source_in_s: float = Field(ge=0)
    source_out_s: float
    output_start_s: float = Field(ge=0)
    output_end_s: float
    speed: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _spans_are_consistent(self) -> Self:
        output_span = self.output_end_s - self.output_start_s
        source_span = self.source_out_s - self.source_in_s
        if output_span <= 0:
            raise ValueError("output_end_s must be after output_start_s")
        if abs(source_span - output_span * self.speed) > TIME_TOLERANCE_S:
            raise ValueError("source span must equal output span times speed")
        return self


class EffectEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: Effect
    start_s: float = Field(ge=0)
    duration_s: float = Field(gt=0)


class TextEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    start_s: float = Field(ge=0)
    end_s: float
    style: TextStyle = TextStyle()

    @model_validator(mode="after")
    def _ends_after_start(self) -> Self:
        if self.end_s <= self.start_s:
            raise ValueError("end_s must be after start_s")
        return self


class Timeline(BaseModel):
    """A render plan in output time (seconds from the start of the video and of the snippet audio).

    Segments are contiguous: the first starts at 0, each starts where the previous one ends,
    and the last ends at `duration_s`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    duration_s: float = Field(gt=0)
    look: Look
    segments: tuple[Segment, ...]
    effects: tuple[EffectEvent, ...] = ()
    texts: tuple[TextEvent, ...] = ()

    @model_validator(mode="after")
    def _segments_cover_duration(self) -> Self:
        if not self.segments:
            raise ValueError("a timeline needs at least one segment")
        expected_start = 0.0
        for segment in self.segments:
            if abs(segment.output_start_s - expected_start) > TIME_TOLERANCE_S:
                raise ValueError(
                    f"segment starts at {segment.output_start_s}, not {expected_start}"
                )
            expected_start = segment.output_end_s
        if abs(expected_start - self.duration_s) > TIME_TOLERANCE_S:
            raise ValueError(f"segments end at {expected_start}, not {self.duration_s}")
        return self

    @model_validator(mode="after")
    def _events_within_duration(self) -> Self:
        event_ends = [effect.start_s + effect.duration_s for effect in self.effects]
        event_ends += [text.end_s for text in self.texts]
        if any(end > self.duration_s + TIME_TOLERANCE_S for end in event_ends):
            raise ValueError("effects and texts must end within the timeline")
        return self

    @property
    def shot_ids(self) -> list[int]:
        return [segment.shot_id for segment in self.segments]


class RenderPlan(BaseModel):
    """Everything `ace render` needs to create a `renders` row and its video."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recipe_id: str
    snippet_id: int
    timeline: Timeline
    targets: tuple[Platform, ...] = Field(min_length=1)
    captions: dict[Platform, str]

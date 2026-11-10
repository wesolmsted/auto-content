import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from engine.recipes.models import (
    Effect,
    EffectEvent,
    Look,
    Recipe,
    Segment,
    TextEvent,
    Timeline,
)

BASE_PARAMS: dict[str, Any] = {
    "cut_rate": "every_2_beats",
    "hard_cut_on_drop": True,
    "opening_shot": "most_motion",
    "footage_category": "city_night",
    "footage_orientation": "portrait",
    "length_s": 12,
    "on_screen_text": "hook_lyric",
    "text_style": {"position": "lower_third", "case": "lower"},
    "snippet_type": "drop",
    "look": "warm",
    "effects": ["zoom_punch_on_downbeat"],
}


def make_recipe(**overrides: Any) -> Recipe:
    return Recipe.model_validate(BASE_PARAMS | overrides)


def test_recipe_id_is_rcp_prefix_plus_12_hex_of_sha256_of_sorted_compact_json() -> None:
    canonical = json.dumps(BASE_PARAMS, sort_keys=True, separators=(",", ":"))
    expected = "rcp_" + hashlib.sha256(canonical.encode()).hexdigest()[:12]
    assert make_recipe().id == expected


def test_same_params_in_any_key_order_give_the_same_id() -> None:
    reordered = dict(reversed(list(BASE_PARAMS.items())))
    assert Recipe.model_validate(reordered).id == make_recipe().id


def test_footage_orientation_is_required() -> None:
    params = {key: value for key, value in BASE_PARAMS.items() if key != "footage_orientation"}
    with pytest.raises(ValidationError, match="footage_orientation"):
        Recipe.model_validate(params)


@pytest.mark.parametrize(
    "overrides",
    [
        {"look": "bw"},
        {"length_s": 7},
        {"hard_cut_on_drop": False},
        {"effects": []},
        {"footage_orientation": "landscape"},
        {"text_style": {"position": "top", "case": "lower"}},
    ],
)
def test_different_params_give_a_different_id(overrides: dict[str, Any]) -> None:
    assert make_recipe(**overrides).id != make_recipe().id


def test_source_is_not_part_of_the_id() -> None:
    assert make_recipe(source="reference:7").id == make_recipe().id


def test_effects_are_a_set_so_order_and_duplicates_do_not_change_the_id() -> None:
    one_order = make_recipe(effects=["flash", "shake", "flash"])
    other_order = make_recipe(effects=["shake", "flash"])
    assert one_order.id == other_order.id
    assert one_order.effects == (Effect.FLASH, Effect.SHAKE)


def segment(start: float, end: float, shot_id: int = 1) -> Segment:
    return Segment(
        shot_id=shot_id,
        source_path=Path(f"shot_{shot_id}.mp4"),
        source_in_s=0.0,
        source_out_s=end - start,
        output_start_s=start,
        output_end_s=end,
    )


def timeline(*segments: Segment, duration_s: float = 2.0, **extra: Any) -> Timeline:
    return Timeline(duration_s=duration_s, look=Look.NATURAL, segments=segments, **extra)


def test_contiguous_timeline_is_valid_and_lists_shot_ids_in_order() -> None:
    plan = timeline(segment(0.0, 0.5, shot_id=3), segment(0.5, 2.0, shot_id=1))
    assert plan.shot_ids == [3, 1]


@pytest.mark.parametrize(
    "segments",
    [
        (),
        (segment(0.1, 2.0),),
        (segment(0.0, 0.5), segment(0.6, 2.0)),
        (segment(0.0, 0.5), segment(0.4, 2.0)),
        (segment(0.0, 0.5), segment(0.5, 1.9)),
    ],
    ids=["empty", "late_start", "gap", "overlap", "short_end"],
)
def test_timeline_rejects_non_contiguous_segments(segments: tuple[Segment, ...]) -> None:
    with pytest.raises(ValidationError):
        timeline(*segments)


def test_segment_source_span_must_match_output_span_times_speed() -> None:
    slow_motion = Segment(
        shot_id=1,
        source_path=Path("s.mp4"),
        source_in_s=1.0,
        source_out_s=1.5,
        output_start_s=0.0,
        output_end_s=1.0,
        speed=0.5,
    )
    assert slow_motion.speed == 0.5
    with pytest.raises(ValidationError):
        Segment(
            shot_id=1,
            source_path=Path("s.mp4"),
            source_in_s=1.0,
            source_out_s=1.5,
            output_start_s=0.0,
            output_end_s=1.0,
        )


def test_effects_and_text_must_end_within_the_timeline() -> None:
    timeline(
        segment(0.0, 2.0),
        effects=(EffectEvent(effect=Effect.FLASH, start_s=1.8, duration_s=0.2),),
        texts=(TextEvent(text="hi", start_s=0.0, end_s=2.0),),
    )
    with pytest.raises(ValidationError):
        timeline(
            segment(0.0, 2.0),
            effects=(EffectEvent(effect=Effect.FLASH, start_s=1.9, duration_s=0.2),),
        )
    with pytest.raises(ValidationError):
        timeline(segment(0.0, 2.0), texts=(TextEvent(text="hi", start_s=1.0, end_s=2.5),))


def test_text_event_must_end_after_it_starts() -> None:
    with pytest.raises(ValidationError):
        TextEvent(text="hi", start_s=1.0, end_s=1.0)

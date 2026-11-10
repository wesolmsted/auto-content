from pathlib import Path
from typing import Any

import pytest

from engine.db.enums import Orientation
from engine.ingest.models import Shot, ShotTags
from engine.recipes.assembly import assemble
from engine.recipes.models import Effect, EffectEvent, Recipe, TextEvent, TextStyle, Timeline
from engine.recipes.slots import NotEnoughShotsError
from engine.snippets.models import BeatGrid


def make_grid(bpm: float = 120.0, duration_s: float = 20.0, drop_s: float | None = 8.0) -> BeatGrid:
    """Beats from t=0 every 60/bpm s, a downbeat every 4th beat."""
    spacing = 60.0 / bpm
    count = int(duration_s / spacing + 1e-9)
    beats = tuple(round(index * spacing, 6) for index in range(count))
    return BeatGrid(
        bpm=bpm, beats=beats, downbeats=beats[::4], drop_s=drop_s, duration_s=duration_s
    )


def make_recipe(**overrides: Any) -> Recipe:
    params: dict[str, Any] = {
        "cut_rate": "every_2_beats",
        "hard_cut_on_drop": True,
        "opening_shot": "most_motion",
        "footage_category": "mixed",
        "footage_orientation": "portrait",
        "length_s": 7,
        "on_screen_text": "none",
        "snippet_type": "drop",
        "look": "warm",
        "effects": [],
    }
    return Recipe.model_validate(params | overrides)


def make_shot(shot_id: int, duration_s: float = 10.0) -> Shot:
    return Shot(
        id=shot_id,
        footage_file_id=1,
        start_s=100.0,
        end_s=100.0 + duration_s,
        width=1080,
        height=1920,
        fps=30.0,
        orientation=Orientation.PORTRAIT,
        motion_score=0.5,
        quality_score=0.8,
        tags=ShotTags(
            category="city_night",
            subject="person",
            setting="street",
            lighting="night",
            mood="energetic",
            has_face=False,
            quality=0.8,
        ),
        proxy_path=Path(f"/media/shots/{shot_id}.mp4"),
    )


def make_shots(count: int, duration_s: float = 10.0) -> list[Shot]:
    return [make_shot(shot_id, duration_s) for shot_id in range(1, count + 1)]


def cut_times(timeline: Timeline) -> list[float]:
    return [segment.output_start_s for segment in timeline.segments[1:]]


def test_every_2_beats_cuts_on_every_other_beat() -> None:
    timeline = assemble(make_recipe(cut_rate="every_2_beats"), make_shots(20), make_grid())
    assert timeline.duration_s == 7.0
    assert cut_times(timeline) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_every_beat_cuts_on_every_beat() -> None:
    timeline = assemble(make_recipe(cut_rate="every_beat"), make_shots(20), make_grid())
    assert cut_times(timeline) == [0.5 * beat for beat in range(1, 14)]


def test_downbeats_cuts_on_downbeats_only() -> None:
    timeline = assemble(make_recipe(cut_rate="downbeats", length_s=12), make_shots(20), make_grid())
    assert timeline.duration_s == 12.0
    assert cut_times(timeline) == [2.0, 4.0, 6.0, 8.0, 10.0]


def test_one_long_shot_has_a_single_segment_without_a_drop() -> None:
    timeline = assemble(
        make_recipe(cut_rate="one_long_shot"), make_shots(3), make_grid(drop_s=None)
    )
    assert [(s.output_start_s, s.output_end_s) for s in timeline.segments] == [(0.0, 7.0)]


def test_one_long_shot_still_cuts_at_the_drop() -> None:
    timeline = assemble(make_recipe(cut_rate="one_long_shot"), make_shots(3), make_grid(drop_s=5.0))
    assert cut_times(timeline) == [5.0]


def test_hard_cut_on_drop_adds_an_off_grid_drop_cut() -> None:
    timeline = assemble(
        make_recipe(cut_rate="downbeats", length_s=12), make_shots(20), make_grid(drop_s=8.25)
    )
    assert cut_times(timeline) == [2.0, 4.0, 6.0, 8.0, 8.25, 10.0]


def test_drop_cut_replaces_a_beat_cut_closer_than_the_minimum_segment() -> None:
    timeline = assemble(
        make_recipe(cut_rate="downbeats", length_s=12), make_shots(20), make_grid(drop_s=8.05)
    )
    assert cut_times(timeline) == [2.0, 4.0, 6.0, 8.05, 10.0]


def test_no_drop_cut_without_hard_cut_on_drop() -> None:
    recipe = make_recipe(cut_rate="downbeats", length_s=12, hard_cut_on_drop=False)
    timeline = assemble(recipe, make_shots(20), make_grid(drop_s=8.25))
    assert cut_times(timeline) == [2.0, 4.0, 6.0, 8.0, 10.0]


def test_drop_after_the_video_end_adds_no_cut() -> None:
    timeline = assemble(make_recipe(cut_rate="one_long_shot"), make_shots(3), make_grid())
    assert cut_times(timeline) == []


def test_length_snaps_to_the_nearest_beat() -> None:
    timeline = assemble(make_recipe(length_s=7), make_shots(20), make_grid(bpm=100.0))
    assert timeline.duration_s == pytest.approx(7.2)


def test_length_longer_than_the_snippet_snaps_to_its_last_beat() -> None:
    timeline = assemble(
        make_recipe(length_s=7), make_shots(20), make_grid(duration_s=5.0, drop_s=None)
    )
    assert timeline.duration_s == 4.5


def test_segments_use_shots_in_order_from_their_proxy_start() -> None:
    shots = make_shots(10)
    timeline = assemble(make_recipe(), shots, make_grid())
    assert timeline.shot_ids == [1, 2, 3, 4, 5, 6, 7]
    first = timeline.segments[0]
    assert first.source_path == Path("/media/shots/1.mp4")
    assert (first.source_in_s, first.source_out_s) == (0.0, 1.0)
    assert first.speed == 1.0


def test_shots_shorter_than_their_slot_are_skipped_and_used_later_if_they_fit() -> None:
    recipe = make_recipe(cut_rate="downbeats", length_s=4, hard_cut_on_drop=False)
    grid = make_grid()
    shots = [make_shot(1, duration_s=1.0), make_shot(2), make_shot(3)]
    timeline = assemble(recipe, shots, grid)
    assert timeline.shot_ids == [2, 3]

    long_then_short_slot = make_recipe(cut_rate="downbeats", length_s=3, hard_cut_on_drop=False)
    assert assemble(long_then_short_slot, [make_shot(1, 1.0), make_shot(2)], grid).shot_ids == [
        2,
        1,
    ]


def test_a_shot_passed_twice_is_used_once() -> None:
    shot = make_shot(1)
    with pytest.raises(NotEnoughShotsError):
        assemble(make_recipe(length_s=2), [shot, shot], make_grid())


def test_raises_when_the_shots_cannot_fill_every_slot() -> None:
    with pytest.raises(NotEnoughShotsError, match="slot 7"):
        assemble(make_recipe(), make_shots(6), make_grid())


def test_raises_when_no_shot_is_long_enough() -> None:
    with pytest.raises(NotEnoughShotsError):
        assemble(make_recipe(cut_rate="one_long_shot"), make_shots(5, duration_s=6.0), make_grid())


def test_unstored_shots_are_rejected() -> None:
    unstored = make_shot(1).model_copy(update={"id": None})
    with pytest.raises(ValueError, match="id"):
        assemble(make_recipe(length_s=1), [unstored], make_grid())


def test_zoom_punch_fires_on_each_downbeat_inside_the_video() -> None:
    recipe = make_recipe(effects=["zoom_punch_on_downbeat"])
    timeline = assemble(recipe, make_shots(20), make_grid())
    assert timeline.effects == tuple(
        EffectEvent(effect=Effect.ZOOM_PUNCH_ON_DOWNBEAT, start_s=start, duration_s=0.2)
        for start in (0.0, 2.0, 4.0, 6.0)
    )


def test_shake_fires_on_every_beat_and_speed_ramp_lasts_one_beat() -> None:
    recipe = make_recipe(length_s=2, effects=["shake", "speed_ramp"])
    timeline = assemble(recipe, make_shots(20), make_grid())
    shakes = [event.start_s for event in timeline.effects if event.effect is Effect.SHAKE]
    ramps = [event for event in timeline.effects if event.effect is Effect.SPEED_RAMP]
    assert shakes == [0.0, 0.5, 1.0, 1.5]
    assert ramps == [EffectEvent(effect=Effect.SPEED_RAMP, start_s=0.0, duration_s=0.5)]


def test_effect_events_are_clipped_to_the_video_end() -> None:
    recipe = make_recipe(cut_rate="every_beat", length_s=2, effects=["speed_ramp"])
    grid = BeatGrid(
        bpm=60.0, beats=(0.0, 1.0, 1.8, 2.0), downbeats=(0.0, 1.8), drop_s=None, duration_s=3.0
    )
    timeline = assemble(recipe, make_shots(5), grid)
    last = timeline.effects[-1]
    assert (last.effect, last.start_s) == (Effect.SPEED_RAMP, 1.8)
    assert last.duration_s == pytest.approx(0.2)


def test_no_effects_when_the_recipe_has_none() -> None:
    assert assemble(make_recipe(), make_shots(20), make_grid()).effects == ()


def test_text_covers_the_whole_video_in_the_recipe_style() -> None:
    style = {"position": "top", "case": "upper"}
    recipe = make_recipe(on_screen_text="hook_lyric", text_style=style)
    timeline = assemble(recipe, make_shots(20), make_grid(), text="we own the night")
    assert timeline.texts == (
        TextEvent(
            text="we own the night",
            start_s=0.0,
            end_s=7.0,
            style=TextStyle.model_validate(style),
        ),
    )


@pytest.mark.parametrize(
    ("on_screen_text", "text"),
    [("none", "ignored"), ("pov_caption", None), ("pov_caption", "  ")],
)
def test_no_text_unless_the_recipe_wants_it_and_text_is_given(
    on_screen_text: str, text: str | None
) -> None:
    recipe = make_recipe(on_screen_text=on_screen_text)
    assert assemble(recipe, make_shots(20), make_grid(), text=text).texts == ()


def test_look_is_carried_from_the_recipe() -> None:
    assert assemble(make_recipe(look="bw"), make_shots(20), make_grid()).look == "bw"

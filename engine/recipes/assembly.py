from collections.abc import Sequence

from engine.ingest.models import Shot
from engine.recipes.models import (
    CutRate,
    Effect,
    EffectEvent,
    OnScreenText,
    Recipe,
    Segment,
    TextEvent,
    Timeline,
)
from engine.recipes.slots import (
    NotEnoughShotsError,
    Slot,
    fit_long_shots,
    plan_slots,
    target_duration,
)
from engine.snippets.models import BeatGrid

EFFECT_DURATION_S = {
    Effect.ZOOM_PUNCH_ON_DOWNBEAT: 0.2,
    Effect.FLASH: 0.1,
    Effect.SHAKE: 0.15,
}


def assemble(
    recipe: Recipe,
    shots: Sequence[Shot],
    beat_grid: BeatGrid,
    text: str | None = None,
) -> Timeline:
    """Pure: cut `shots` (in the order from `select_shots`) to the beat grid (DESIGN.md §7.3).

    Cut points follow `recipe.cut_rate`, with a cut on `beat_grid.drop_s` when
    `recipe.hard_cut_on_drop`; total length is `recipe.length_s` snapped to the nearest beat.
    `one_long_shot` runs each shot as long as it can and cuts on the last beat it covers (see
    `fit_long_shots`). Otherwise each slot takes the first unused shot at least as long as the
    slot, so a shorter shot is skipped (and stays available for a later slot). No shot is used
    twice. Effects are keyed to beat/downbeat times. `text` is the on-screen text for
    `recipe.on_screen_text` (unused for `none`).
    """
    duration_s = target_duration(recipe.length_s, beat_grid)
    if recipe.cut_rate is CutRate.ONE_LONG_SHOT:
        segments = _fill_long_shots(recipe, beat_grid, shots)
    else:
        segments = _fill_slots(recipe, plan_slots(recipe, beat_grid), shots)
    return Timeline(
        duration_s=duration_s,
        look=recipe.look,
        segments=segments,
        effects=_effect_events(recipe.effects, beat_grid, duration_s),
        texts=_text_events(recipe, text, duration_s),
    )


def _fill_slots(recipe: Recipe, slots: list[Slot], shots: Sequence[Shot]) -> tuple[Segment, ...]:
    unused = _distinct(shots)
    segments = []
    for index, slot in enumerate(slots):
        shot = next((shot for shot in unused if shot.duration_s >= slot.length_s), None)
        if shot is None:
            raise NotEnoughShotsError(
                f"recipe {recipe.id}: no unused shot is at least {slot.length_s:.2f} s long "
                f"for slot {index + 1} of {len(slots)}"
            )
        unused.remove(shot)
        segments.append(_segment(shot, slot))
    return tuple(segments)


def _fill_long_shots(
    recipe: Recipe, beat_grid: BeatGrid, shots: Sequence[Shot]
) -> tuple[Segment, ...]:
    distinct = _distinct(shots)
    fitted = fit_long_shots(recipe, beat_grid, [shot.duration_s for shot in distinct])
    return tuple(_segment(distinct[index], slot) for index, slot in fitted)


def _distinct(shots: Sequence[Shot]) -> list[Shot]:
    return list({_stored_id(shot): shot for shot in shots}.values())


def _segment(shot: Shot, slot: Slot) -> Segment:
    return Segment(
        shot_id=_stored_id(shot),
        source_path=shot.proxy_path,
        source_in_s=0.0,
        source_out_s=slot.length_s,
        output_start_s=slot.start_s,
        output_end_s=slot.end_s,
    )


def _stored_id(shot: Shot) -> int:
    if shot.id is None:
        raise ValueError("shots must be stored (have an id) before assembly")
    return shot.id


def _effect_events(
    effects: Sequence[Effect], beat_grid: BeatGrid, duration_s: float
) -> tuple[EffectEvent, ...]:
    events = []
    for effect in effects:
        times = beat_grid.beats if effect is Effect.SHAKE else beat_grid.downbeats
        length_s = EFFECT_DURATION_S.get(effect, 60.0 / beat_grid.bpm)
        events += [
            EffectEvent(effect=effect, start_s=start, duration_s=min(length_s, duration_s - start))
            for start in times
            if start < duration_s
        ]
    return tuple(sorted(events, key=lambda event: (event.start_s, event.effect)))


def _text_events(recipe: Recipe, text: str | None, duration_s: float) -> tuple[TextEvent, ...]:
    if recipe.on_screen_text is OnScreenText.NONE or text is None or not text.strip():
        return ()
    return (TextEvent(text=text, start_s=0.0, end_s=duration_s, style=recipe.text_style),)

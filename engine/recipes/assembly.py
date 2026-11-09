from collections.abc import Sequence

from engine.ingest.models import Shot
from engine.recipes.models import (
    Effect,
    EffectEvent,
    OnScreenText,
    Recipe,
    Segment,
    TextEvent,
    Timeline,
)
from engine.recipes.slots import NotEnoughShotsError, Slot, plan_slots
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
    Each slot takes the first unused shot at least as long as the slot, so a shorter shot is
    skipped (and stays available for a later slot); no shot is used twice. Effects are keyed to
    beat/downbeat times. `text` is the on-screen text for `recipe.on_screen_text` (unused for
    `none`).
    """
    slots = plan_slots(recipe, beat_grid)
    duration_s = slots[-1].end_s
    return Timeline(
        duration_s=duration_s,
        look=recipe.look,
        segments=_fill_slots(recipe, slots, shots),
        effects=_effect_events(recipe.effects, beat_grid, duration_s),
        texts=_text_events(recipe, text, duration_s),
    )


def _fill_slots(recipe: Recipe, slots: list[Slot], shots: Sequence[Shot]) -> tuple[Segment, ...]:
    unused = list({_stored_id(shot): shot for shot in shots}.values())
    segments = []
    for index, slot in enumerate(slots):
        shot = next((shot for shot in unused if shot.duration_s >= slot.length_s), None)
        if shot is None:
            raise NotEnoughShotsError(
                f"recipe {recipe.id}: no unused shot is at least {slot.length_s:.2f} s long "
                f"for slot {index + 1} of {len(slots)}"
            )
        unused.remove(shot)
        segments.append(
            Segment(
                shot_id=_stored_id(shot),
                source_path=shot.proxy_path,
                source_in_s=0.0,
                source_out_s=slot.length_s,
                output_start_s=slot.start_s,
                output_end_s=slot.end_s,
            )
        )
    return tuple(segments)


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

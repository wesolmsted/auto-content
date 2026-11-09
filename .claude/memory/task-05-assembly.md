---
name: task-05-assembly
description: Slice 5 recipes/assembly: exact semantics of select_shots, assemble, plan_slots and plan_batch, invariants tested, deviations and gotchas
type: project
---

Slice 5 built pure shot selection and timeline assembly, plus the Phase 1 batch planner, in `engine/recipes/`.

**Why:** render (slice 6) and the `ace render batch` wiring consume these outputs, and their rules aren't obvious from the signatures. **How to apply:** call these functions as described here. Change the rules only together with their tests.

## `engine/recipes/slots.py` (shared by selection and assembly)
- `plan_slots(recipe, beat_grid) -> list[Slot]`: `Slot(start_s, end_s)` plus `.length_s`, covering 0 to the video length contiguously.
- `target_duration(length_s, grid)`: the beat > 0 nearest to `length_s`; on a tie, the earlier beat. A snippet shorter than `length_s` gives its last beat. With no beat > 0, it falls back to `min(length_s, grid.duration_s)`.
- `cut_points` per `cut_rate`:
  - `every_beat`: all beats.
  - `every_2_beats`: `beats[::2]` (beat indexes 0, 2, 4, …, counting from the grid's first beat).
  - `downbeats`: the downbeats.
  - `one_long_shot`: no grid cuts.
- Only cuts within `[MIN_SEGMENT_S, duration - MIN_SEGMENT_S]` count (`MIN_SEGMENT_S = 0.1`).
- Drop cut: when `hard_cut_on_drop` and `drop_s` falls inside that range, `drop_s` is added and any grid cut within 0.1 s of it is removed. A drop past the snapped end gives no cut. That's common: the default 7 s recipe has no drop cut when the drop sits 8 s into the snippet.
- `NotEnoughShotsError(ValueError)`: the error both functions raise when shots run out.

## `select_shots(recipe, library, beat_grid, rng, settings, now)`
- Eligible shots:
  - category: `tags.category == footage_category`, or any category for `mixed`;
  - quality: `quality_score >= quality_floor`;
  - cooldown: `last_used_at` is None or `now - last_used_at >= shot_cooldown_days` (with a cooldown of 0, nothing is excluded).
- The eligible pool is shuffled with `rng`, then:
  - slot 0 gets the top-ranked opener that's at least as long as the slot. Ranks, descending, with ties broken by the shuffle:
    - `most_motion`: `motion_score`;
    - `person_face`: `(has_face, quality_score)`;
    - `scenic`: `(is_scenic, quality_score)`, where scenic is category `nature`, setting in {nature, beach, rooftop}, or subject in {landscape, architecture};
    - `text_led`: `(mood == "calm", -motion_score)`.
  - The other slots, longest first, each take the first remaining shuffled shot that's long enough. Fit sets are nested, so this greedy pass is optimal; only the opener choice can make it fail when a fill was possible.
- Returns exactly one distinct shot per slot, in timeline order, so `assemble` on the result uses them 1:1 in order.

## `assemble(recipe, shots, beat_grid, text=None) -> Timeline`
- Each slot takes the first unused shot (in the given order) with `duration_s >= slot length`. Shorter shots are skipped but stay available for later, shorter slots. Duplicate shot ids are collapsed. Raises `NotEnoughShotsError` naming the slot, and `ValueError` for a shot with `id=None`.
- Segment: `source_in_s=0`, `source_out_s=slot length` (from the proxy start), `speed=1.0`, `source_path=shot.proxy_path`.
- Effects, for each beat time `t < duration`, with each event clipped to the video end:
  - `zoom_punch_on_downbeat` 0.2 s and `flash` 0.1 s on every downbeat;
  - `shake` 0.15 s on every beat;
  - `speed_ramp` for one beat (60/bpm) on every downbeat. It is only an `EffectEvent`: segment speed stays 1, and the renderer decides what a ramp looks like.
- Effect events are sorted by `(start_s, effect)`.
- Text: if `on_screen_text != none` and `text` isn't blank, one `TextEvent` from 0 to the end in `recipe.text_style`. Otherwise no text events.

## `engine/recipes/batch.py`: `plan_batch(session, settings: Settings, rng, now, n) -> list[RenderPlan]`
- Inputs:
  - `settings.recipes.fixed`;
  - active snippets whose song has a `bpm`. The grid is the song's beats/downbeats inside `[start_s, end_s]` shifted to snippet time, and `drop_s - start_s` when the drop falls in the window;
  - shots with `proxy_path`, all five tag columns, `motion_score` and `quality_score` set.
- Each plan:
  - draws the shuffled (recipe, snippet with matching `snippet_type`) pairs whose recipe is under `max_repeats_per_week`. That counts non-failed renders with `created_at > now - 7 d`, plus this batch.
  - tries pairs until `select_shots` succeeds. A failing pair is logged and skipped. When no pair works, the batch stops early (and may be empty).
  - marks the shots it used with `last_used_at = now` in memory, so the cooldown keeps them out of later plans.
- Targets: always `ig_trial`. The first `tiktok_renders_per_day` plans of the owner's local day also get `tiktok`. The day is in `notify.timezone`, and non-failed renders already created today with "tiktok" in `targets_json` count toward the limit. With `tiktok_every_render`, every plan gets `tiktok`.
- Captions: `captions.templates[target].format(song=title, snippet=label or "")` for each target.
- Writes nothing: no renders, no recipe rows, no `use_count`/`last_used_at` updates. The render/integration step must insert the `recipes` row (FK) and update the shots' usage.
- Passes `text=None` to `assemble`, since there's no lyric/caption source yet. The fixed recipes all use `on_screen_text = none`.

## Invariants tested
`tests/unit/test_recipes_properties.py`, with Hypothesis over 60–180 BPM grids with ±20% jitter, random drops and every recipe value:
- duration equals the nearest beat to `length_s` (±1 frame);
- every cut is on the recipe's grid subset, or exactly at the drop;
- there's a drop cut when one is required;
- no shot is reused, and too-short decoy shots are never used;
- segments are contiguous from 0 to `duration_s`, with source in/out inside the shot;
- effects and texts fall inside the video;
- selected shots are eligible and assemble 1:1 in order;
- selection is deterministic for a given seed.

Mutation-checked: each property fails for its matching bug.

## Gotchas
- `one_long_shot` with no in-video drop needs a single shot as long as the whole video. With `ingest.max_shot_s = 8`, it can't be filled for lengths of 12 or 20, so the pair is skipped. None of the fixed recipes use it.
- A 4-render batch can need up to 56 distinct shots (the every_beat/7 s recipe uses 14 slots). A small library yields fewer plans, not an error.
- `_beat_grid` in batch.py duplicates what slice 4's `beat_grid_for_snippet` will do. Feature modules shouldn't import each other's internals, and that function was a stub here. Unify them if they ever drift.
- `ShotTags.quality` is filled from `quality_score`, and `issues` isn't read back from `tags_json`; selection uses neither.
- Run: `uv run pytest tests/unit/test_recipes_*.py tests/integration/test_recipes_batch.py`.

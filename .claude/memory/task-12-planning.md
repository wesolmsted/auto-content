---
name: task-12-planning
description: Task 12 planning changes: per-recipe balance, drop-in-video snippet preference, chained one_long_shot, render batch exit code, render one ignoring limits; new keyword args and DESIGN deviations
type: project
---

Task 12 changed how `plan_batch` picks recipes and snippets, how `one_long_shot` is cut, and two CLI behaviours (owner questions Q5, Q6, Q7, Q11, Q12).

**Why:** in a real run the 20 s hook recipe was never picked, `one_long_shot` could never fill 12 or 20 s with `ingest.max_shot_s = 8`, a failed render still exited 0, and the dev loop hit the weekly cap. **How to apply:** these rules replace the "Each plan" and `one_long_shot` notes in task-05-assembly.md and the `ace render one` gotcha in task-11-render-batch.md.

## `plan_batch` (engine/recipes/batch.py)
- **Recipe first (Q7).** For each plan, the recipes under the weekly cap are sorted by renders in the last 7 days (non-failed, this batch included), with ties broken by `rng.random()`. The first recipe that one of its snippets and the library can fill gets the plan. Before, (recipe, snippet) pairs were shuffled uniformly, so a recipe with more matching snippets won more often.
- **Snippet preference (Q6).** A recipe's matching snippets are shuffled with `rng`, then stably sorted so those whose drop (relative to the snippet start) is `<= target_duration(recipe.length_s, grid)` come first. It's a preference, not a filter: the others are still tried. Hook snippets usually have no drop, so they all rank equally.
- New keyword `ignore_limits: bool = False` skips both `recipes.max_repeats_per_week` and the shot cooldown (passed to `select_shots(ignore_cooldown=...)`). Settings aren't mutated.

## `one_long_shot` (Q5)
- `engine/recipes/slots.py: fit_long_shots(recipe, beat_grid, shot_durations) -> list[tuple[int, Slot]]` is used by both `select_shots` and `assemble`.
- The video is split at its forced cuts: the drop, with `hard_cut_on_drop` and the drop inside `[0.1, duration - 0.1]`. Each section is filled from its start. At each point, the first unused shot (in the given order) that reaches the section end, or a beat at least `MIN_SEGMENT_S` in, is used. The cut goes on the **last beat the shot still covers**, never later than `section_end - MIN_SEGMENT_S`. Cuts may land on **any beat**, not just downbeats.
- `select_shots`: the eligible pool is shuffled (for the tie-break), then sorted longest first. `opening_shot` is ignored for `one_long_shot`. Reaching a beat gets easier as shots get longer, so if the longest remaining shot can't fit, nothing can, and it raises `NotEnoughShotsError`.
- `assemble`: walks the given shots in order (deduped), skipping a shot that reaches no beat from the current point. With `select_shots`' output, it uses them 1:1 in order.
- `plan_slots` for `one_long_shot` still returns only the forced-cut sections. Don't use it to size a `one_long_shot` timeline.

## CLI (engine/render/cli.py)
- `ace render batch` prints the `planned= ready= failed= notified=` line, then exits 1 if `failed > 0` (Q11).
- `ace render one` calls `run_render_batch(..., ignore_limits=True)` (Q12). `render batch` keeps both limits.
- `run_render_batch(..., *, snippet_id=None, ignore_limits=False)` passes the flag through to `plan_batch`.

## Deviations for DESIGN.md (main agent to write)
- §7.3: `select_shots` gains `ignore_cooldown`, and `plan_batch` gains `ignore_limits`. Add the recipe-first balance and the drop-in-video snippet preference. `one_long_shot` chains the longest shots, cut on the last covered beat, and ignores `opening_shot`.
- §11 invariant "every cut on the grid": for `one_long_shot`, the grid is every beat. The property test `allowed_cuts` maps `ONE_LONG_SHOT` to `grid.beats`. A new property checks longest-first order, on-beat cuts, the drop cut, segment length within the shot, and that no covered beat was skipped.

## Gotchas
- `ignore_limits` with `n > 1` can reuse a shot within the batch. The in-batch "used now" marking relies on the cooldown, which is off. Only `render one` (n=1) uses it.
- The old unit test `test_raises_when_no_shot_is_long_enough` used `one_long_shot` with 6 s shots on a 7 s video. That now fills (6.0 + 1.0), so the test uses `every_2_beats` with 0.5 s shots instead. `test_one_long_shot_raises_when_no_shot_reaches_a_beat` covers the `one_long_shot` failure.
- The balance tests are parametrised over seeds. The old uniform-pairs planner fails them for every seed tried.
- Run: `uv run pytest tests/unit/test_recipes_*.py tests/integration/test_recipes_batch.py tests/integration/test_render_batch_cli.py`.

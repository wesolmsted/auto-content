---
name: task-11-render-batch
description: Task 11 render-batch integration: run_render_batch flow, ace render batch/one usage, shot_rows location, beat-grid consolidation, the e2e test, deviations and gotchas
type: project
---

Task 11 wired planning, rendering, shot usage and notifications into `ace render batch` and `ace render one`.

**Why:** this is the daily 06:00 job (DESIGN §3), and later slices (hand-off, measure, learn) start from the rows it writes. **How to apply:** call `run_render_batch` and don't re-wire plan/execute/notify yourself. Read the gotchas before changing the CLI's exit codes or how it builds the notifier.

## `engine/render/batch_job.py`
`run_render_batch(session, settings, store, notifier: Notifier | None, rng, now, n, *, snippet_id=None) -> BatchSummary(planned, ready, failed, notified)`
1. `plan_batch(session, settings, rng, now, n, snippet_id=...)`.
2. For each plan:
   - insert the `recipes` row if it's missing (`id` = recipe hash, `params_json` = `canonical_params()`, `source` = `recipe.source`). The recipe is looked up by id in `settings.recipes.fixed`.
   - `execute_plan`. It commits the recipe row together with the render row, and never raises for render errors.
   - if the render is `ready`, add 1 to `use_count` for each shot in `timeline.shot_ids` and set `last_used_at = now`. A `failed` render leaves its shots untouched. Commit, then move on to the next plan.
3. If there's a notifier: run `send_pending_handoff_notifications` once, then commit. Inside quiet hours it sends nothing, so a 06:00 batch's notifications wait for a later `ace notify pending` or batch run in the 4–7 pm ET window. With `notifier=None`, nothing is sent and `notified_at` stays NULL.

## CLI (`engine/render/cli.py`)
- `ace render batch [--n N] [--recipe ID] [--snippet ID] [--dry-run]`
  - `--n` defaults to `cadence.renders_per_day`.
  - `--recipe` narrows `settings.recipes.fixed` to that recipe. An id that isn't in it exits 1 with `unknown recipe <id>`.
  - `--snippet` is passed to `plan_batch(snippet_id=...)`.
  - `--dry-run` prints `plan k: recipe=… snippet=… targets=ig_trial,tiktok duration=12.00s`, then a `  cuts:` line and a `  shots:` line. It writes nothing and sends nothing.
  - Otherwise it prints `planned= ready= failed= notified=` and exits 1 if any render failed (task 12). Failures are on the rows.
  - The notifier is built with `ntfy_notifier_from_settings`. If ntfy isn't configured, the batch still renders: it logs a warning and prints `notifications skipped: ntfy is not configured: set …`.
  - A `ValueError` exits 1 with its message. The main case is ntfy configured but `HANDOFF_BASE_URL` unset during the posting window: the renders are already committed, and only the notifications fail.
- `ace render one --recipe ID --snippet ID` runs `run_render_batch` with n=1, that recipe and snippet, and no notifier.
  - It prints `render <id> ready: <path>`, or exits 1 with the render's error.
  - It exits 1 with `nothing to render` when the pair can't be planned: the snippet type doesn't match, or there aren't enough eligible shots. It ignores the weekly cap and shot cooldown (task 12).
- Both use an unseeded `random.Random()`, so a dry run shows a sample batch, not the exact one a later real run will make.

## Moved code
- `shot_to_row` and `shot_from_row` now live together in `engine/ingest/shot_rows.py`. `engine/recipes/shot_rows.py` is deleted, and `engine/ingest/job.py` imports `shot_to_row` from the new module.
- The round-trip test was renamed to `tests/integration/test_shot_rows_roundtrip.py`.
- `plan_batch`'s `_beat_grid` now builds a `SongAnalysis` from the song row and calls slice 4's `beat_grid_for_snippet`. A stored `drop_s` outside `[start_s, end_s]` is passed as None, because that function raises for one. Snippets whose song has no `duration_s` are now skipped, like those with no `bpm`. Tested in `tests/integration/test_render_batch_drop_window.py`.

## Tests
- `tests/integration/test_render_batch_job.py` is the end-to-end test on real ffmpeg, about 30 s.
  - Shots: real `build_shots` on the portrait fixture with `FakeVisionTagger`, built twice as footage files 1 and 2. That gives 8 shots of 2.5 s, module-scoped.
  - Snippet: the click track analysed with `analyze_song` and stored through `add_song`/`add_snippet`; drop window 2–14 s, with the drop from `detect_drop`.
  - It covers:
    - 17:30 ET: 2 `ready` renders that validate, a `recipes` row, `use_count` = 1 on the 8 used shots, and 2 notifications sent;
    - 09:00 ET: 0 sent and `notified_at` NULL;
    - one broken proxy: 1 ready, 1 failed, and the failed render's shots stay unused;
    - no notifier;
    - a rerun that reuses the recipe row and adds to `use_count`.
- `tests/integration/test_render_batch_cli.py` covers the CLI. It uses 60 rows whose proxy is the landscape fixture, the 20 s click track, and respx for ntfy.
- `tests/integration/test_render_batch_snippet_filter.py` covers `snippet_id`.
- Run: `uv run pytest tests/integration/test_render_batch_*.py tests/integration/test_shot_rows_roundtrip.py`.

## Deviations / gotchas
- `plan_batch` gained a keyword-only `snippet_id` filter. `--snippet` and `ace render one` need it, and there was no other way to make it work without duplicating the planner.
- The e2e test uses its own recipe (downbeats, 7 s, warm, so 4 slots of 2 s or less). The 3 fixed recipes need 10–14 distinct shots each, and the portrait fixture gives only 4 shots per build.
- `ace render one` goes through `plan_batch` with `ignore_limits=True` (task 12): TikTok targeting applies, the weekly cap and shot cooldown don't.
- Rendering needs `render.luts_dir` to resolve. The default is relative (`config/luts`), so run from the repo root or set an absolute path. Tests set it with `ACE_RENDER__LUTS_DIR`.
- No fixed recipe uses text, so the local Homebrew ffmpeg (which has no `drawtext`) renders the whole batch.

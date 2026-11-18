---
name: project-status
description: Where work stopped (2026-11-09), open owner questions with recommendations, owner action items, and the next tasks to run
type: project
---

**State as of 2026-11-09 (evening):** Phase 1 is merged, and the first real test run is done (Drive ingest → snippets → render → ntfy → hand-off page on localhost). The owner's answers to Q1–12 are applied (tasks 12–15 plus the config fix on main). `make check` passes 628 tests.

**Why:** lets the next session resume without re-deriving context.
**How to apply:** read this first; check the open items with the owner before changing the behavior they cover.

## Real-run findings (2026-11-09)
- Footage: 3 files → 88 shots. 75 are landscape, from a music video with fast cuts (avg 1.3 s); 13 are portrait. Ingest took 8 min.
- Portrait recipes and the 20 s downbeats recipe can't be filled yet. Each needs 13–16 distinct shots, and the 20 s recipe needs shots of 1.74 s or longer. The owner needs more portrait and longer clips.
- Song "you dont dance": 137.9 BPM, drops at 42.23 s and 111.87 s. Active snippets 4–6 (1–3 were deactivated, since they predate start snapping).
- `footage_orientation` became a recipe variable at the owner's request; landscape stays center-cropped for now.

## Owner environment (set up 2026-11-09; no secrets here)
- Done: Google OAuth (Desktop client, token in `data/secrets/`), Drive footage Inbox/Processed ids in `config/local.toml`, `ANTHROPIC_API_KEY`, ntfy topic (app subscription verified), `ffmpeg-full`.
- `HANDOFF_BASE_URL` is `http://127.0.0.1:8000` (Mac only); Tailscale isn't set up, so notification links don't open on the phone yet.
- Song audio lives in `~/.config/auto-content/audio/`; the Google client JSON is outside the repo too.
- Meta developer app: not started. (TikTok was dropped from scope on 2026-11-14, DESIGN D11; `.env` can lose its empty `TIKTOK_*` lines.)
- Step-by-step setup guide (Claude doc): https://claude.ai/artifact/UhXZwgqQrk7g2Vr8RyEXLx
- Local DB (2026-11-14): at migration 0002. Renders 1–5 and their recipe rows were deleted unreviewed, with render files removed and shot `use_count`/`last_used_at` reset. Kept: 3 footage files, 88 tagged shots, 1 song, snippets 1–6 (1–3 inactive). Pre-cleanup backup in `tmp/engine-before-cleanup-2026-11-14.sqlite3`.
- To test notifications outside 16:00–19:00 ET, set `ACE_NOTIFY__QUIET_HOURS__START=00:00:00` and `ACE_NOTIFY__QUIET_HOURS__END=00:00:00`.

## Open items
- Tests that call `load_settings(REPO_CONFIG_DIR)` also read the owner's git-ignored `config/local.toml`, so local overrides leak into them (found 2026-11-18 when `micro.font_file` changed a micro args test; that test now clears it). Fix: a test helper that loads `default.toml` only.
- Q13 caption-match `min_score`: tune with real data.
- A render whose notification keeps failing stays `ready` forever; expiry doesn't run when ntfy isn't configured (task-14 questions).
- `render one` with `ignore_limits` could reuse a shot when n > 1 (only n = 1 uses it today).
- `one_long_shot` ignores `opening_shot`.
- The hand-off page is localhost-only until the owner sets up Tailscale (setup guide doc, step 6).

## Micro edits (built 2026-11-15, DESIGN §7.10, D12)
- Slices 16–20 are merged. They haven't been run against real Drive yet.
- Owner steps to try it:
  1. `ace init` applies migration 0003.
  2. Put a `<song>_micro` folder (videos plus one `.txt`) in the footage inbox.
  3. Run `ace micro run`, then `ace micro list`.
- `ace micro run` should go on the launchd schedule every 15 min. It exits 1 if ntfy isn't configured.
- Open choices kept as built:
  - a duplicate stays `duplicate` after its original is removed;
  - a removed set's local videos are kept;
  - a saved micro render leaves the hand-off list;
  - micro text uses the default white-with-outline style.

## Next tasks
1. Render a fresh batch (`ace render batch`), owner reviews it through the hand-off flow; tune from that feedback.
2. Shared-file follow-ups: add `tzdata`; add `engine/render/fonts/*` to package data; resolve `luts_dir`/`font_file` against the config dir; bump `measure.meta_graph_url` after checking the real account.
3. Native-looking text (DESIGN §7.4).
4. launchd schedule (DESIGN §3).
5. Meta developer app, then measure; then Phase 2.

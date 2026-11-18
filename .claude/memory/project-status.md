---
name: project-status
description: Where work stopped (2026-11-18), owner environment, open items, micro edits status and the next tasks to run
type: project
---

**State as of 2026-11-18 (midday):** Phase 1 and micro edits (slices 16–20) are merged. Micro edits had their first real run on 2026-11-18 and the owner approved the result ("looks good"). `make check` passes 774 tests. Only `main` exists locally and on origin; all task branches and worktrees were deleted after merging.

**Earlier:** the first real recipe run was 2026-11-09 (Drive ingest → snippets → render → ntfy → hand-off page on localhost); the owner's answers to Q1–12 are applied (tasks 12–15).

**Why:** lets the next session resume without re-deriving context.
**How to apply:** read this first; check the open items with the owner before changing the behavior they cover.

## Real-run findings (2026-11-09)
- Footage: 3 files → 88 shots. 75 are landscape, from a music video with fast cuts (avg 1.3 s); 13 are portrait. Ingest took 8 min.
- Portrait recipes and the 20 s downbeats recipe can't be filled yet. Each needs 13–16 distinct shots, and the 20 s recipe needs shots of 1.74 s or longer. The owner needs more portrait and longer clips.
- Song "you dont dance": 137.9 BPM, drops at 42.23 s and 111.87 s. Active snippets 4–6 (1–3 were deactivated, since they predate start snapping).
- `footage_orientation` became a recipe variable at the owner's request; landscape stays center-cropped for now.

## Owner environment (set up 2026-11-09; no secrets here)
- Done: Google OAuth (Desktop client, token in `data/secrets/`; re-consented 2026-11-18 after the 7-day Testing expiry, see [[google-token-expiry]]; the app isn't published yet, so it will expire again around 2026-11-25), Drive footage Inbox/Processed ids in `config/local.toml`, `ANTHROPIC_API_KEY`, ntfy topic (app subscription verified), `ffmpeg-full`.
- `HANDOFF_BASE_URL` is `http://127.0.0.1:8000` (Mac only); Tailscale isn't set up, so notification links don't open on the phone yet.
- Song audio lives in `~/.config/auto-content/audio/`; the Google client JSON is outside the repo too.
- Meta developer app: not started. (TikTok was dropped from scope on 2026-11-14, DESIGN D11; `.env` can lose its empty `TIKTOK_*` lines.)
- Step-by-step setup guide (Claude doc): https://claude.ai/artifact/UhXZwgqQrk7g2Vr8RyEXLx
- Local DB (2026-11-18): at migration 0005 (head). Backups from today's steps are in `tmp/engine-before-*-2026-11-18.sqlite3`.
- Micro font: `config/local.toml` has `[micro] font_file = "~/.config/auto-content/fonts/Helvetica-Bold.ttf"`, the Bold face extracted from macOS `Helvetica.ttc` with `tmp/extract_helvetica_bold.py` (run via `uvx --from fonttools`). Not in git (licensed font).
- Recipe renders (2026-11-14): renders 1–5 and their recipe rows were deleted unreviewed, with render files removed and shot `use_count`/`last_used_at` reset. Kept: 3 footage files, 88 tagged shots, 1 song, snippets 1–6 (1–3 inactive). Pre-cleanup backup in `tmp/engine-before-cleanup-2026-11-14.sqlite3`.
- To test notifications outside 16:00–19:00 ET, set `ACE_NOTIFY__QUIET_HOURS__START=00:00:00` and `ACE_NOTIFY__QUIET_HOURS__END=00:00:00`.

## Open items
- Tests that call `load_settings(REPO_CONFIG_DIR)` also read the owner's git-ignored `config/local.toml`, so local overrides leak into them (found 2026-11-18 when `micro.font_file` changed a micro args test; that test now clears it). Fix: a test helper that loads `default.toml` only.
- Q13 caption-match `min_score`: tune with real data.
- A render whose notification keeps failing stays `ready` forever; expiry doesn't run when ntfy isn't configured (task-14 questions).
- `render one` with `ignore_limits` could reuse a shot when n > 1 (only n = 1 uses it today).
- `one_long_shot` ignores `opening_shot`.
- The hand-off page is localhost-only until the owner sets up Tailscale (setup guide doc, step 6).

## Micro edits (built 2026-11-15, DESIGN §7.10, D12)
- **First real run 2026-11-18**, set `youdontdance_micro` in the Drive inbox: 3 square (1080×1080) videos with lyrics, `.txt` as in `docs/examples/youdontdance.txt`.
- Changes made from the owner's feedback that day (all in DESIGN §7.10 and the decision log):
  - optional `.txt` lines `caption:` (stored for future auto-uploads, unused), `cutoff:` (on-screen text ends at this source time) and `mirror: off`; migrations 0004 and 0005;
  - the source keeps its aspect ratio with black bars (no crop to 9:16);
  - text in Helvetica Bold, white with a black stroke (`micro.font_file`).
- Today's slots (2026-11-18) are rendered and `ready`, all unmirrored: id 1 is the 11:45 ET test slot (notified), id 2 is 13:00 and id 3 is 18:00. These only notify if `ace micro run` runs after their slot.
- To test a slot now, override the slots: `ACE_MICRO__POST_TIMES='["HH:MM:00","13:00:00","18:00:00"]' uv run ace micro run`, with a time from the last 30 min (ET).
- To re-render a day: back up the DB, delete the `micro_renders` rows and `data/media/renders/micro_<id>.mp4` files, reset `micro_sets.next_text_index`/`last_used_at` and `micro_sources.last_used_at`, then run again. SQLite reuses the deleted ids.
- `ace micro run` should go on the launchd schedule every 15 min. It exits 1 if ntfy isn't configured.
- Open choices kept as built:
  - a duplicate stays `duplicate` after its original is removed;
  - a removed set's local videos are kept;
  - a saved micro render leaves the hand-off list;

## Next tasks
0. Micro: set up the launchd job for `ace micro run` every 15 min, so slots notify on their own. Publish the Google OAuth app so the Drive token stops expiring weekly. Set up Tailscale so notification links open on the phone.
1. Render a fresh batch (`ace render batch`), owner reviews it through the hand-off flow; tune from that feedback.
2. Shared-file follow-ups: add `tzdata`; add `engine/render/fonts/*` to package data; resolve `luts_dir`/`font_file` against the config dir; bump `measure.meta_graph_url` after checking the real account.
3. Native-looking text (DESIGN §7.4).
4. launchd schedule (DESIGN §3).
5. Meta developer app, then measure; then Phase 2.

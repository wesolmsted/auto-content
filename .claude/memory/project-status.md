---
name: project-status
description: Where work stopped (2026-11-09), open owner questions with recommendations, owner action items, and the next tasks to run
type: project
---

**State as of 2026-11-09:** Phase 1 (DESIGN.md §12, slices 1–10 plus the render-batch integration) is fully merged to `main`. `make check` passes: 560 tests, 1 skipped (drawtext unavailable in local Homebrew ffmpeg). The owner paused work here. Nothing has been run against real accounts yet.

**Why:** lets the next session resume without re-deriving context.
**How to apply:** read this first. Ask the owner the open questions before changing the behavior they cover, then run the next tasks with the parallel agent workflow in CLAUDE.md.

## Open questions for the owner (recommendation in bold; not yet answered)
1. **ntfy server:** default `NTFY_SERVER` to `https://ntfy.sh`? **Yes.**
2. **Notification title:** keep "Hand-off ready: <song> · <cut rate>, <look>"? **Keep for now.**
3. **Problem files:** a file with zero usable shots = failure, and scratch files of failed downloads are kept. **OK.**
4. **Drop detection:** auto-detect for every snippet type, and use a manual `--drop` exactly as typed. **OK.**
5. **`one_long_shot`:** use the longest shots and cut only when one runs out. **Yes.**
6. **Drop in short videos:** the planner prefers snippets whose drop falls inside the video length. **Yes.**
7. **Recipe balance:** balance choice per recipe, not per recipe×snippet pair. **Yes.**
8. **Vision refusal fallbacks:** leave off for the prototype. **Yes.**
9. **Expiry clock:** count hand-off expiry from `notified_at`, not `created_at`. **Yes.**
10. **Expiry check:** run `supersede_expired` at the start of `ace notify pending`; it has no caller yet. **Yes.**
11. **Failed renders:** `ace render batch` exits non-zero if any render failed. **Yes.**
12. **Dev-loop limits:** `ace render one` skips the weekly cap and shot cooldown. **Yes.**
13. **Caption matching:** with weights caption 0.7 / time 0.3 and `min_score` 0.3, a post whose caption shares no words with the provided one rarely auto-matches. Keep it, relying on manual link as the fallback, or lower `min_score`? Undecided; tune with real data.

## Owner action items
- **Now:**
  - Open `/hooks` or restart Claude Code, so the repo auto-approve hook loads.
  - `brew install ffmpeg-full`, so on-screen text renders locally; set `tools.ffmpeg` if needed.
  - Check GitHub Actions/Docker, or install `gh` so Claude can.
- **Before the first real run:**
  - a Google OAuth desktop client (`GOOGLE_OAUTH_CLIENT_JSON`), then `ace auth google`, and set the Drive folder ids;
  - `ANTHROPIC_API_KEY`;
  - the ntfy app + topic (task-07 memory);
  - Tailscale + `HANDOFF_BASE_URL`/`HANDOFF_TOKEN` (task-08 memory);
  - Meta and TikTok developer apps with read scopes (task-09 memory).
- **Later:** real songs and footage for tuning; one sample TikTok Studio export before Phase 2.

## Next tasks (not started)
1. **Apply the owner's answers** to questions 1–12.
2. **Shared-file follow-ups:**
   - bump `measure.meta_graph_url` (currently v23.0) after checking the real account;
   - optionally add `error`/`attempts` to `posts`;
   - add `tzdata` as a dependency;
   - add `engine/render/fonts/*` to package data;
   - resolve `luts_dir`/`font_file` against the config dir (`ace` currently has to run from the repo root);
   - optionally move the snippet analysis constants into config.
3. **Native-looking text** (DESIGN §7.4):
   - bundle TikTok Sans (OFL) plus the closest OFL match for Instagram;
   - style presets: shadow, outline, rounded box;
   - safe zones and wrapping;
   - `ace snippets add --text`, with `plan_batch` passing hook text.

   Needs `ffmpeg-full` locally.
4. **Schedule:** launchd jobs per the DESIGN §3 schedule, and a first real end-to-end run with the owner.
5. **Then Phase 2:** TikTok export import, the learning loop, the weekly report.

Agent worktrees from Phase 1 remain under `.claude/worktrees/` (gitignored). Their branches are all merged and can be removed.

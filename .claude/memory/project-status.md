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

## Open items
- Q13 caption-match `min_score`: tune with real data.
- A render whose notification keeps failing stays `ready` forever; expiry doesn't run when ntfy isn't configured (task-14 questions).
- `render one` with `ignore_limits` could reuse a shot when n > 1 (only n = 1 uses it today).
- `one_long_shot` ignores `opening_shot`.
- The hand-off page is localhost-only until the owner sets up Tailscale (setup guide doc, step 6).

## Next tasks
1. Owner reviews renders 1–5 and the hand-off flow; tune from that feedback.
2. Shared-file follow-ups: add `tzdata`; add `engine/render/fonts/*` to package data; resolve `luts_dir`/`font_file` against the config dir; bump `measure.meta_graph_url` after checking the real account.
3. Native-looking text (DESIGN §7.4).
4. launchd schedule (DESIGN §3).
5. Meta/TikTok developer apps, then measure; then Phase 2.

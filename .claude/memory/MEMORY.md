# Project memory index

One line per memory file in this directory: `- [Title](file.md): one-line hook`.

Each memory file holds one fact, with frontmatter:

```markdown
---
name: short-kebab-case-slug
description: one-line summary used to decide relevance
type: project | reference | feedback
---

The fact. For project/feedback entries, add **Why:** and **How to apply:** lines.
```

Don't store anything already in the code, docs/DESIGN.md, README.md or git history, and never store secrets.

## Memories

- [Prototype first](prototype-first.md): build the simplest working version end to end; defer refinements until usage shows they're needed
- [Task 01: skeleton](task-01-skeleton.md): map of shared files, contracts, stub signatures, fixture ground truth, checks and gotchas. Read before any slice work
- [Task 02: Drive ingest](task-02-drive-ingest.md): GoogleDriveClient, ace auth google, poll_footage, run_footage_ingest (pluggable build_shots), FakeDriveClient, status flow, OAuth setup
- [Task 03: shot library](task-03-shot-library.md): build_shots (scene split, motion/quality, 9:16 proxies), ClaudeVisionTagger + prompt contract, FakeVisionTagger
- [Task 04: snippets](task-04-snippets.md): analyze_song / detect_drop / beat_grid_for_snippet, tempo correction + measured accuracy, song cache, ace snippets add/list
- [Task 05: assembly](task-05-assembly.md): select_shots, assemble (cut/drop/length rules), plan_batch, shot_from_row, tested timeline invariants
- [Task 06: render](task-06-render.md): render/validate/execute_plan, filter graph, effects, LUTs, TextRenderStyle, Anton font, drawtext needs ffmpeg-full locally
- [Task 07: notify](task-07-notify.md): NtfyNotifier, quiet hours (4–7 pm ET window), pending hand-off notifications, FakeNotifier, owner's ntfy setup steps
- [Task 08: hand-off page](task-08-handoff.md): routes, cookie auth, download = approval transaction, link-post URL parsing (IG shortcode/TikTok id), expiry, Tailscale serve runbook
- [Project status](project-status.md): first real run done 2026-11-09, findings, open items, next tasks. Read first when resuming
- [Task 09: measure](task-09-measure.md): read-only Meta/TikTok readers, match_posts, run_measure, ace measure due / posts link / auth meta|tiktok, unverified API details, owner's developer-app setup
- [Task 11: render batch](task-11-render-batch.md): run_render_batch flow, ace render batch/one, shot_rows now in engine/ingest/shot_rows.py, beat-grid consolidation, e2e test
- [Task 13: ingest lock](task-13-ingest-lock.md): ingest commits per file and step so slow downloads/moves don't lock SQLite; how the lock test reproduces it
- [Task 14: expiry](task-14-expiry.md): expiry from notified_at, unnotified never expire, ace notify pending supersedes before sending
- [Task 15: snippet start](task-15-snippet-start.md): snap_start_to_downbeat, snippets add stores the snapped start; old snippets must be re-added
- [Task 12: planning](task-12-planning.md): recipe-first balance, in-video drop preference, chained one_long_shot (fit_long_shots), ignore_limits/ignore_cooldown, render batch exit 1 on failure

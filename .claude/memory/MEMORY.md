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
- [Task 04: snippets](task-04-snippets.md): analyze_song / detect_drop / beat_grid_for_snippet, tempo correction + measured accuracy, song cache, ace snippets add/list
- [Task 07: notify](task-07-notify.md): NtfyNotifier, quiet hours (4–7 pm ET window), pending hand-off notifications, FakeNotifier, owner's ntfy setup steps

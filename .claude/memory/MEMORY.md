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

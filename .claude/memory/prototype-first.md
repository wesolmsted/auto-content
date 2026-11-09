---
name: prototype-first
description: Default to the simplest working prototype; add features/refinements only once real usage shows they're needed
type: feedback
---

Default to getting a working prototype end to end before adding more features or robustness layers. Propose the simplest approach that could work. Note possible refinements as deferred items (DESIGN.md §14 open questions) instead of building or designing them up front.

**Why:** The owner rejected frame-fingerprint post matching as "over-engineering before we know if the other checks will work most of the time" (2026-11-08). They want evidence from real usage before adding complexity.

**How to apply:** When designing or coding, ship the minimal version of each slice first, with a way to measure whether it's good enough (e.g. a match rate in `ace status`). Mention a refinement in one line as a possible follow-up; don't add it to the plan unless asked. Applies together with the test-first workflow in CLAUDE.md.

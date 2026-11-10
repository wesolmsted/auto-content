---
name: task-14-expiry
description: Slice 14 expiry: supersede_expired counts from notified_at (NULL never expires), wired into ace notify pending; DESIGN wording to update
type: project
---

Slice 14 fixed the hand-off expiry clock and wired expiry into a job (owner decisions Q9 and Q10).

**Why:** notifications are held for quiet hours (sent only 16:00–19:00 New York), so counting from `created_at` cut the owner's window short, by up to about 10 h for a render made at 06:00. Before this, nothing called `supersede_expired`. **How to apply:** expiry runs only through `ace notify pending`. Schedule that command, and don't call `supersede_expired` anywhere else.

## Public API
- `engine/handoff/expiry.py`: `supersede_expired(session, settings, now) -> int`, with the same signature as before. It moves `ready` renders with `notified_at < now - handoff.expire_hours` to `superseded`. It flushes and doesn't commit.
  - Exactly at the cutoff the render stays `ready` (the comparison is strict).
  - `notified_at` NULL never expires. In SQL, `NULL < x` is not true, so no special case is needed.
- `ace notify pending` uses one `now` and one `session_scope`. It first runs `supersede_expired`, then `send_pending_handoff_notifications`, and commits both together. It prints `superseded N expired render(s)` and then `sent N hand-off notification(s)`.
  - Expiry runs inside quiet hours too: only sending is held.
  - The command still exits 1 before touching the DB when ntfy config is missing, so a missing `NTFY_TOPIC` also stops expiry.

## DESIGN.md wording the main agent must update
- §5 render state diagram (around line 236): `ready --> superseded: not downloaded within handoff.expire_hours` → "not downloaded within handoff.expire_hours of notified_at".
- §7.5 (around line 354): "Renders not downloaded within `handoff.expire_hours` (default 72) become `superseded`." → add "counted from `notified_at`; a `ready` render not notified yet never expires. `ace notify pending` runs expiry before sending."
- Remove the deviation "Expiry counts from `renders.created_at`, not `notified_at`" from `task-08-handoff.md`, and its "Nothing calls it yet" note.
- Add a §13 decision-log entry for Q9/Q10.

## Gotchas
- A render whose notification keeps failing (`notified_at` NULL) stays `ready` forever. That is intended: the owner never saw it.
- Tests:
  - `uv run pytest tests/integration/test_handoff_expiry.py tests/integration/test_notify_cli.py`;
  - the CLI test seeds renders with `notified_at` relative to the real wall clock, because the CLI uses `datetime.now(UTC)`.

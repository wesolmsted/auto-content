---
name: task-15-snippet-start
description: `ace snippets add` snaps --start to the nearest downbeat before --end (so renders start on a bar); old snippets keep unsnapped starts and must be re-added
type: project
---

`ace snippets add` now stores `start_s` snapped to a downbeat of the analyzed song. The first real run started every render mid-beat (e.g. `--start 38` on a 137.9 BPM song put the first beat at 38.39 s).

**Why:** the video starts at the snippet start, so an off-beat start puts the first cut 0.3–0.4 s in. **How to apply:** don't re-snap downstream; `snippets.start_s` is already a downbeat for snippets added after this change.

## Public API
- `analysis.snap_start_to_downbeat(downbeats, start_s, end_s) -> float`. Pure. Returns the downbeat nearest `start_s` among those strictly before `end_s`, picking the earlier one on a tie. Raises `ValueError` ("no downbeat before …") if there is none.

## CLI behavior (`engine/snippets/cli.py`)
- Order: the typed window is checked (`end > start`, inside the song). Then start is snapped, and the CLI prints `start snapped 38.00 → 38.36 s (downbeat)` only when the value changes. Then `--drop` is validated against the snapped window, or the drop is auto-detected on it.
- `--end` is stored as typed (the render length already snaps to beats).
- A snapped start can be earlier than the typed one, so a `--drop` slightly before the typed start is now accepted.

## Deviation from the brief
The brief said "if the nearest downbeat isn't strictly before --end, use the nearest downbeat inside the window". Read literally, that fallback can never succeed: any downbeat in [start, end) is closer to start than one at or past end. So the rule is implemented as "nearest downbeat strictly before end". In the fallback case it moves start *earlier* than typed. For example, `--start 5.6 --end 6.0` with downbeats at 4 and 6 gives start 4.0, not an error.

## Gotchas
- **Snippets added before this change keep their unsnapped `start_s`.** There is no migration. The owner must re-add them (`ace snippets add` again with the same args) and stop using the old rows.
- On the click fixture, `--start 4` is stored as 3.9938 (the analyzed downbeat), and `snippets list` shows `3.99`.
- Tests: `tests/unit/test_snippets_snap_start.py`, plus the snapping tests at the end of `tests/integration/test_snippets_cli.py`.

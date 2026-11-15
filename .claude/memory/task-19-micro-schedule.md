---
name: task-19-micro-schedule
description: Slice 19 micro schedule: slots and set/video rotation, run_micro_renders, micro expiry and notifications, ace micro ingest/render/notify/run/list, test fakes and gotchas
type: project
---

Slice 19 built the micro schedule (DESIGN §7.10 **Schedule** and **Notify**) and the `ace micro` commands.

**Why:** `ace micro run` is the 15-minute job that turns micro sets into one notified render per daily slot. **How to apply:** call the functions below and don't re-implement slot or rotation logic. Inject the renderer and edit sampler in tests.

## Public API
- `engine/micro/schedule.py` (pure):
  - `slots_for_day(now, post_times, timezone)`: the UTC slots on the local date of `now`.
  - `slots_to_fill(now, post_times, timezone, late_grace_minutes, filled)`: slots with no render that are at most the grace past. Exactly at the grace boundary still counts.
  - `pick_set_and_video(sets, videos)`: the least recently used `active` set with a `ready` video, then its least recently used `ready` video. It filters statuses itself, so callers can pass any rows.
  - `take_next_text(micro_set)`: returns the set's next text and increments `next_text_index`.
- `engine/micro/job.py`: `run_micro_renders(session, store, settings, now, rng, *, renderer=render_micro, edit_sampler=sample_edit, duration_of=output_duration_s) -> MicroRenderSummary(slots, ready, failed, no_source)`.
  - Order per slot: pick, take text, set `last_used_at = scheduled_for` on the set and the video, sample the edit, insert a `rendering` row, commit, then render.
  - Retries up to `render.max_attempts`. `error`/`attempts`/`ffmpeg_argv_json` are recorded on the row (the argv comes from `FfmpegError`).
  - If no set has a ready video, it stops and counts the remaining slots as `no_source`. Those slots stay open for a later run.
- `engine/micro/notify.py` (flushes, never commits):
  - `supersede_expired_micro(session, settings, now)`: strict `notified_at < now - micro.expire_hours`.
  - `send_due_micro_notifications(session, notifier, settings, now)`: `ready`, `notified_at` NULL, `scheduled_for <= now`.
    - Quiet hours are ignored.
    - It raises `ValueError` if `HANDOFF_BASE_URL` is unset.
    - A failed send stays pending.
  - `micro_notification(render, base_url)`:
    - title `Micro ready: <caption>`;
    - body `10s · <text>`;
    - click `<base>/micro/<id>`;
    - tags `("scissors",)`.

## CLI (`engine/micro/cli.py`)
- **`ingest`**:
  - needs only `drive.footage_inbox_folder_id`, since nothing moves to processed;
  - builds the Drive client with `engine.ingest.cli.make_drive_client`;
  - prints the `MicroIngestSummary` fields as `name=count`.
- **`render`** prints `slots= ready= failed= no_source=` and exits 1 if any render failed.
- **`notify`** prints `superseded N expired micro render(s)`, then `sent N micro notification(s)`. With ntfy unconfigured it exits 1 before touching the DB, so expiry doesn't run either (same as `ace notify pending`).
- **`run`** runs ingest, render and notify. A failed step prints `micro <step> failed` (a typer exit) or `micro <step> failed: <Type>: <msg>` (an unexpected exception), and the later steps still run. At the end it exits 1 if any step failed.
- **`list`** prints:
  - per set: `set <id> <name> status=… song="…"|- texts=N next_text="…"|- [error="…"]`;
  - under each set, one line per video: `  video <id> <name> status=… renders=N`, or `  no videos`.

## Deviations
- None in behavior. One assumption: the caption falls back to the set's folder name if `song_title` is NULL. That can't happen for an `active` set.
- New shared test helper `tests/fakes/micro.py`: `fixed_edit`, `fixed_duration_s`, `FakeMicroRenderer(failures=N)`, `add_micro_set`, `add_micro_video`. It wasn't in the assigned file list, so the main agent should confirm it.

## Gotchas and testing
- Tests patch module-level names in `engine.micro.cli`: `now_utc`, `render_micro`, `sample_edit`, `output_duration_s`, `ntfy_notifier_from_settings`, `make_drive_client` and `poll_micro_sets`. The CLI passes the renderer and sampler explicitly for that reason: default arguments are bound when the function is defined.
- Logs go to stderr as JSON. CLI tests compare `result.stdout`, not `result.output`.
- SQLAlchemy 2.1: `dict(session.execute(...).tuples())` fails, because `Result` has `keys()`. Build the dict with a comprehension.
- Run: `uv run pytest tests/unit/test_micro_schedule.py tests/integration/test_micro_*.py`. `test_micro_day.py` walks two days in New York and covers renders, notifications and the strict 24 h expiry.

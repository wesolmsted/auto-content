---
name: task-07-notify
description: Slice 7 notify: NtfyNotifier, quiet hours, pending hand-off notifications job, FakeNotifier, ace notify test/pending, ntfy setup, gotchas
type: project
---

Slice 7 built phone notifications for ready renders.

**Why:** the owner learns a render is ready from a push notification that opens the hand-off page (DESIGN §7.5). **How to apply:** to notify, call `send_pending_handoff_notifications` (or `ace notify pending`) after renders reach `ready`. Don't send notifications any other way.

## Public API
- `engine/notify/ntfy.py`:
  - `NtfyNotifier(server, topic, token, client, *, max_retries, backoff_base_s, sleep=time.sleep)`. The four positional parameters are the skeleton's; the retry keywords are required.
  - `ntfy_notifier_from_settings(settings, client)` reads `NTFY_SERVER`, `NTFY_TOPIC`, `NTFY_TOKEN`, `notify.max_retries` and `http.backoff_base_s`. It raises `ValueError` naming the missing variable(s). Build the notifier with this; pass a client made with `httpx.Client(timeout=settings.notify.timeout_s)`.
- `engine/notify/quiet_hours.py`: `is_quiet(moment, notify_settings) -> bool` and `next_send_time(now, notify_settings) -> datetime` (UTC). Both are pure.
- `engine/notify/handoff.py`:
  - `send_pending_handoff_notifications(session, notifier, settings: Settings, now) -> int`;
  - `handoff_notification(render_row, song_title, recipe_params, base_url) -> Notification`;
  - `recipe_short_name(params)`.
- CLI:
  - `ace notify test` sends a test notification whose click URL is `HANDOFF_BASE_URL`, if set;
  - `ace notify pending` runs the job once and commits.

  Both exit 1 with a message when config is missing or the send fails.

## ntfy request
- `POST {server}/{topic}`. The body is the UTF-8 message text.
- Headers:
  - `Title`: the title, sent as an RFC 2047 `=?UTF-8?B?…?=` encoded word when it is not ASCII. The default title contains `·`, so it is always encoded. ntfy decodes encoded words.
  - `Click` when a click URL is set;
  - `Tags`, comma-separated;
  - `Authorization: Bearer <token>` only when `NTFY_TOKEN` is set.
- A hand-off notification has:
  - title `Hand-off ready: <song> · <cut rate>, <look>` (e.g. "every beat, high contrast");
  - body `7s for IG Trial + TikTok`;
  - click URL `HANDOFF_BASE_URL/handoff/<id>`, without a doubled slash;
  - tags `clapper` plus the target values (`ig_trial`, `tiktok`). ntfy shows tags that aren't emoji names as text.

## Retries and failures
- Requests go through `engine.http.request_with_retries`: 429, 5xx and transport errors are retried with backoff; 4xx fails at once.
- A final failure raises `NotificationError("ntfy returned HTTP 403")` or `NotificationError("ntfy unreachable (ConnectError)")`. The error is raised outside the `except` block, so the httpx error, whose message holds the URL and therefore the topic, is not chained.
- The job catches `NotificationError` per render, logs a warning with `row_id`, leaves `notified_at` NULL and continues. The next run retries. Other exceptions propagate.
- The job flushes after each send but never commits; the caller commits (`session_scope`). If the process crashes between a send and the commit, the owner gets a duplicate notification later. This is acceptable.

## Quiet hours
- The window is `[start, end)` in local time in `notify.timezone`.
- `start > end` wraps past midnight. `start == end`, or `quiet_hours` unset, means no window.
- The owner posts only 4–7 pm Eastern. On main, the default is `America/New_York` with quiet hours from 19:00 to 16:00, so notifications go out only between 16:00 and 19:00 local time. Tests cover:
  - 06:00 → held until 16:00 the same day;
  - 17:30 → sent now;
  - 19:30 → held until 16:00 the next day;
  - both DST transitions (across fall back the hold is 22 h).
- Inside the window, the job sends nothing and returns 0; it logs the time it will next send. Nothing is scheduled: the next run after the window ends sends the held renders.
- `next_send_time` returns the window end. With DST:
  - when the end time repeats as clocks fall back, it uses the first occurrence after `now` (fold 0, then fold 1);
  - when the end time falls in the spring-forward gap, the result is up to an hour past the jump. It loops until it is outside the window.
- A Hypothesis property test checks that the result is never before `now`, never quiet, and equals `now` when `now` is not quiet.

## Fake
`tests/fakes/notify.py`: `FakeNotifier(fail_if=None)`.
- `.attempted` records every call;
- `.sent` records only successful calls;
- `fail_if(notification) -> bool` makes a send raise `NotificationError`, e.g. `FakeNotifier(fail_if=lambda n: True)`.

## Owner setup for ntfy (no secrets here)
1. Install the ntfy app (iOS or Android).
2. Choose a long random topic (e.g. `openssl rand -hex 16`). Anyone who knows a topic on ntfy.sh can read it, so treat it as a secret.
3. In the app, subscribe to that topic on `https://ntfy.sh`, or on your server if you self-host.
4. In `.env`, set:
   - `NTFY_SERVER=https://ntfy.sh` (required; there is no Python default);
   - `NTFY_TOPIC=<topic>`;
   - `NTFY_TOKEN` only for a server with access control;
   - `HANDOFF_BASE_URL` to the Mac's Tailscale URL.
5. Set `notify.timezone` in `config/local.toml` to an IANA zone.
6. Run `ace notify test`.

## Gotchas
- httpx logs every request URL at INFO. `engine/notify/ntfy.py` sets the `httpx` and `httpcore` loggers to WARNING when it is imported, so the topic can't leak into logs. That setting belongs in `engine/log.py` (shared); it was reported to the main agent.
- Ambiguous local times never compare equal across zones (PEP 495). Compare datetimes in UTC in tests.
- `zoneinfo` needs system tz data; `tzdata` is not a dependency. macOS, Ubuntu CI and `python:3.12-slim` have it.
- `tests/integration/test_notify_cli.py` sets `ACE_NOTIFY__QUIET_HOURS__START`/`END` to `00:00:00` (an empty window) so the tests don't depend on the wall clock. Reuse that trick in end-to-end tests.
- Tests: `uv run pytest tests/unit/test_notify_*.py tests/integration/test_notify_*.py`. `add_render(session, ...)` in `tests/integration/test_notify_handoff.py` seeds a song, snippet, recipe and render.

---
name: task-08-handoff
description: Slice 8 hand-off page: routes, token-cookie auth, download = approval transaction, reject, link-post URL parsing, expiry, ace serve, Tailscale runbook, gotchas
type: project
---

Slice 8 built the hand-off web page (`engine/handoff/`) and `ace serve`.

**Why:** the owner reviews, saves (approves), rejects and links posts from the phone here (DESIGN §7.5, D9). **How to apply:** call the functions below, and keep their state rules when changing the page.

## Public API
- `engine/handoff/app.py`: `create_app(settings, session_factory, store, clock=lambda: datetime.now(UTC)) -> FastAPI`. `clock` exists so tests can fix `now`.
- `engine/handoff/approval.py`: route-free state changes. Each one flushes and leaves the commit to the caller.
  - `get_render(session, id)` raises `HandoffNotFoundError` (404).
  - `check_downloadable(render)` raises `HandoffConflictError` (409) unless the render is `ready` or `approved`.
  - `record_download(session, render, now)`.
  - `reject_render(session, render, reason, note, now)`.
  - `link_post(session, render, platform, url)`.
- `engine/handoff/links.py`: `parse_post_url(platform, url) -> PostLink(external_id, permalink)`. It is pure and raises `PostUrlError(ValueError)`. The measure agent's `ace posts link` can reuse it.
- `engine/handoff/expiry.py`: `supersede_expired(session, settings, now) -> int`. It flushes but doesn't commit. **Nothing calls it yet**; the main agent has to wire it into a scheduled job.
- `engine/handoff/cli.py`: `ace serve [--host --port]`. Defaults come from `handoff.host`/`handoff.port` (127.0.0.1:8000). It exits 1 when `HANDOFF_TOKEN` is unset or empty. It runs `uvicorn.run(app, host, port)`.

## Routes
All responses are server-rendered Jinja (`templates/`), styled by `static/handoff.css`, with `static/handoff.js` for share and copy.
- `GET /login?token=…`: 303 to `/` and sets cookie `ace_handoff`; 401 for a wrong token or none configured.
- `GET /`: `ready` renders by `created_at` desc, then `approved` renders with at least one `awaiting_post` post, by `downloaded_at` desc.
- `GET /handoff/{id}`: the page. It has:
  - a `<video>` preview;
  - **Save video** (status `ready`/`approved` only);
  - one card per target: caption, **Copy caption**, reminder;
  - after approval, a **Link post** form on each card whose post is linkable, or the permalink once matched;
  - **Reject** (status `ready` only): radio chips, one per `RejectReason`, plus a note.
- `GET /handoff/{id}/preview`: a `FileResponse`. Starlette handles `Range` (206 + `Content-Range`). It never changes state.
- `GET /handoff/{id}/download`: approval, then the file with `Content-Disposition: attachment; filename="render_<id>.mp4"`.
  - 409 unless the render is `ready`/`approved`; 404 if the render or its file is missing.
  - The file is checked **before** anything is written.
- `POST /handoff/{id}/reject` (form `reason`, `note`): 303 back to the page. An unknown reason gives 422; a render that isn't `ready` gives 409.
- `POST /handoff/{id}/link` (form `platform`, `url`): 303 back on success.
  - An unparseable URL gives 400 and re-renders the page with the error under that card.
  - No post for that platform gives 404 (e.g. before approval). A post that is already `matched` gives 409.
- `/docs` and `/openapi.json` are turned off.

## Auth
- An HTTP middleware covers **every** path except `/login`, `/static` included, and returns 401 plain text without the cookie.
- The cookie value is `sha256(HANDOFF_TOKEN)` hex: the token itself crosses the wire only once, at login. Comparisons use `secrets.compare_digest`.
- Cookie flags: `httponly`, `samesite=lax`, a one-year max-age, and **no `secure` flag**, because uvicorn sees plain http behind `tailscale serve`.
- Rotating `HANDOFF_TOKEN` signs every device out.

## Approval transaction (download = approval)
The route runs one `session_scope`: `get_render`, `check_downloadable`, resolve the file, `record_download`, commit, and only then stream the file.
- `record_download` always does `download_count += 1`.
- Only when the status is `ready`, it also:
  - sets `approved`;
  - sets `downloaded_at = now`, which later downloads never change;
  - adds one `PostRow(awaiting_post)` per `targets_json` entry.
- A repeat download only counts. The `UNIQUE(render_id, platform)` constraint is the backstop against duplicate posts. Two truly simultaneous first downloads would make the second one fail with an IntegrityError (500) rather than duplicate posts. That's acceptable for one user.
- Approval doesn't set `reviewed_at`. Reject sets it, plus `reject_reason` and `reject_note` (stripped, with empty stored as NULL).

## Link-post URL parsing rules
The URL is stripped first. The scheme must be http/https; a URL with no scheme is rejected.
- **Instagram** (`ig_trial`):
  - hosts `instagram.com`, `www.`, `m.`;
  - path `/reel/<code>`, `/reels/<code>` or `/<user>/reel/<code>`, with an optional trailing slash, query ignored;
  - code is `[A-Za-z0-9_-]+`;
  - `external_id` = the shortcode, and `permalink` = `https://www.instagram.com/reel/<code>/`.
  - `/p/<code>/` is rejected.
- **TikTok**:
  - hosts `tiktok.com`, `www.`, `m.`;
  - path `[/@user]/video/<digits>`, query ignored;
  - `external_id` = the numeric id, and `permalink` = `https://www.tiktok.com[/@user]/video/<id>`.
  - `vm.`/`vt.tiktok.com` short links are rejected with "open the link and paste the full URL". Resolving them would need a network call.
- Linking sets `matched`, `match_method=manual`, `external_id` and `permalink`. It **leaves `posted_at` NULL**: the URL doesn't carry it.

## Reaching it from the phone over Tailscale (no secrets here)
1. Install Tailscale on the Mac and the phone, and sign both into the same tailnet. Turn on MagicDNS and HTTPS certificates in the admin console.
2. In `.env`, set:
   - `HANDOFF_TOKEN=<openssl rand -hex 32>`;
   - `HANDOFF_BASE_URL=https://<mac-name>.<tailnet>.ts.net`.
3. Run `ace serve`, which binds 127.0.0.1:8000. Then run `tailscale serve --bg 8000` once. Tailscale terminates HTTPS on the tailnet name and proxies to localhost, so the page is never on the LAN or the internet. Don't use `tailscale funnel`, which would make it public.
4. On the phone, open `https://<mac-name>.<tailnet>.ts.net/login?token=<token>` once. The cookie lasts a year. Notification links (`HANDOFF_BASE_URL/handoff/<id>`) then open directly.
5. HTTPS matters: `navigator.clipboard` and the Web Share API only work in a secure context. On plain `http://<tailscale-ip>:8000`, Copy caption falls back to selecting the text, and Save video to a plain download.

## Deviations from DESIGN.md
- The link-post route is `POST /handoff/{id}/link` with a `platform` form field. DESIGN doesn't name it.
- The IG `external_id` from a manual link is the **reel shortcode**, while the Graph API `/media` id is numeric. The measure slice must handle that: match manual IG posts by `permalink`, or resolve the shortcode, before `fetch_metrics`. Manual links also have `posted_at` NULL, so the 24 h/72 h checkpoints need `posted_at` filled in from `recent_videos`.
- Expiry counts from `renders.created_at`, not `notified_at`. With notifications held until 16:00, a render made at 06:00 gets about 62 h after the notification arrives.
- `/static` sits behind the login too, which is stricter than DESIGN asks.

## Gotchas
- With Web Share, the JS **fetches the download** (which approves) and then opens the share sheet. Cancelling the sheet still counts as saved, and the page reloads to show Link post. If `share()` throws anything but `AbortError` (iOS drops the user activation when the fetch is slow), it falls back to navigating to the download URL. That only increments `download_count` again.
- The file is located through `store.path_for(RENDERS, Path(file_path).name)`, so MediaStore stays the authority and the stored directory is ignored.
- `TemplateResponse` renders immediately, so the page context is built inside the open session.
- Tests:
  - `uv run pytest tests/unit/test_handoff_links.py tests/integration/test_handoff_*.py`;
  - `tests/integration/test_handoff_app.py` has a `Handoff` helper (`add_render(status, targets, post_statuses, created_at)`, `render(id)`, `posts(id)`, a settable `clock`) for seeding renders with a real file.

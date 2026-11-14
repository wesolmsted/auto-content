---
name: task-20-micro-handoff
description: Slice 20 micro hand-off: Micro edits list on the index, /micro/{id} page, preview and download routes, micro state changes in engine/handoff/micro.py
type: project
---

Slice 20 added micro renders (DESIGN §7.10, D12) to the hand-off page.

**Why:** the owner saves each micro variant from the phone, like recipe renders, but with no reject, posts or linking. **How to apply:** keep micro state changes in `engine/handoff/micro.py`, separate from `approval.py`.

## Public API
- `engine/handoff/micro.py` (each function flushes; the caller commits):
  - `get_micro_render(session, id)` raises `HandoffNotFoundError` (404).
  - `check_micro_downloadable(render)` raises `HandoffConflictError` (409) unless `ready` or `downloaded` (`MICRO_DOWNLOADABLE`).
  - `record_micro_download(session, render, now)`: always `download_count += 1`; only from `ready` it sets `downloaded` and `downloaded_at = now`.
  - `due_micro_renders(session, now)`: `ready` renders with `scheduled_for <= now` **or** `notified_at` set, ordered `scheduled_for` desc, then id desc.

## Routes (in `engine/handoff/app.py`)
- `GET /`: a *Micro edits* section after the two recipe lists. Each item links to `/micro/{id}` and shows the caption (song) and the on-screen text. Empty text: "No micro edits right now." A slot exactly at `now` shows; a future slot that hasn't been notified doesn't.
- `GET /micro/{id}`: template `micro.html`. It has the preview, **Save video** (`id="save-video"`, so `handoff.js` share-sheet code works unchanged, only when downloadable), one card with the "Turn on Trial before you share it." reminder, the caption with **Copy caption**, and the on-screen text. No reject, no link form.
- `GET /micro/{id}/preview`: `FileResponse` with Range support; changes nothing.
- `GET /micro/{id}/download`: same transaction order as recipe downloads (look up, check status, resolve the file, record, commit, stream). Filename is `micro_<id>.mp4`. 409 for rendering/superseded/failed, 404 for an unknown id or a missing file, with nothing written.
- The file is found by `_video_path`, which now takes `RenderRow | MicroRenderRow`: `store.path_for(RENDERS, Path(file_path).name)`.
- Auth: the existing middleware covers the new routes (tested: 401 without the cookie).

## Deviations from DESIGN.md
- None in behavior. The index also lists `ready` renders that were notified before their slot (as the task asked), which DESIGN doesn't spell out.

## Gotchas and testing
- Index uses the app's `clock` to decide which slots are due, so tests can fix `now`.
- A `downloaded` micro render drops off the index (no "awaiting post" list for micro); its page still offers Save video.
- Tests: `uv run pytest tests/integration/test_handoff_micro.py`. Rows are inserted directly (`MicroSourceRow` + `MicroRenderRow`); the stored `file_path` directory is deliberately wrong to prove MediaStore is the authority.

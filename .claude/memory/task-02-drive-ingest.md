---
name: task-02-drive-ingest
description: Slice 2 (Drive ingest): GoogleDriveClient, ace auth google, poll_footage, run_footage_ingest, FakeDriveClient; how build_shots plugs in, state transitions, OAuth setup, gotchas
type: project
---

Slice 2 built the Drive side of `ace ingest footage` and the job that drives slice 3's `build_shots`.

**Why:** later slices and the main agent need the public API and the behavior that isn't obvious from DESIGN.md. **How to apply:** call these instead of re-implementing; use `FakeDriveClient` in tests.

## Public API
- `engine/drive/google.py`:
  - `GoogleDriveClient(credentials, settings: DriveSettings, *, service=None)`. `service` is an already-built Drive v3 resource, for tests.
  - `DRIVE_SCOPES` (`drive`), `token_path(secrets_dir)` (`google_token.json`), `run_oauth_flow(client_json)`, `save_credentials(creds, path)` (0600), `load_credentials(path, request=None)` (refreshes an expired token and re-saves it; raises `GoogleAuthError` "run `ace auth google`").
- `engine/ingest/poller.py`:
  - `poll_footage(...)` keeps the skeleton signature. It commits after each state change (task 13), so no write transaction spans a download or move.
  - Helpers the job reuses: `footage_folders(settings)` (raises ValueError if an id is unset), `complete_tagged_footage(session, drive, processed_id, max_attempts)`, `advance(row, status)`, `record_failure(row, error, max_attempts) -> bool`.
- `engine/ingest/job.py`:
  - `run_footage_ingest(session, drive, store, tagger, drive_settings, ingest_settings, build=build_shots) -> FootageIngestSummary(poll, built, shots, completed, failed)`;
  - `ShotBuilder` is the type alias of `build_shots`'s signature;
  - the job stores shots with `shot_to_row`, which now lives in `engine/ingest/shot_rows.py` next to its inverse `shot_from_row` (moved there by task 11).
- `engine/ingest/cli.py`: `make_drive_client(settings)` and `make_tagger(settings)` are the factories; tests monkeypatch them.

## How build_shots plugs in
The job calls `build(Path(row.local_path), row.id, tagger, store, ingest_settings)` for each `downloaded` row. If it returns shots, the job stores them and the row goes to `tagged`, both in one commit. Any exception is recorded on the row as `"<ExceptionType>: <message>"`, and the job moves on to the next file. An empty list is recorded as `NoShotsFoundError: no shots found`. Until slice 3 lands, every file ends up in `downloaded` with error `NotImplementedError: ...`.

## State transitions as implemented
- `new` → `downloaded`: the file is downloaded to `scratch/footage_<row id><lowercased suffix>`.
- `downloaded` → `tagged`: the builder succeeded and the shots are stored.
- `tagged` → `done`: the file is moved in Drive, the scratch file deleted, and `local_path` set to None.
- `split` is never used, because `build_shots` splits and tags in one call.
- A new row whose md5 matches any existing row is inserted as `duplicate` and moved to Processed. If that move fails, it is retried on every poll while the file is still listed in the inbox. Duplicates never count attempts.
- `attempts` counts tries at the current step only. `advance` resets it to 0 and clears `error`. Reaching `max_attempts` (`ingest.max_attempts`) sets `failed`. `failed` rows are never retried.
- Files with no md5 are never treated as duplicates.

## FakeDriveClient (`tests/fakes/drive.py`)
- `add_file(folder, file_id, content=b"...", *, name=None, mime_type="video/mp4", md5=<real md5 of content>|None)`;
- `file_ids_in(folder)`;
- `fail_downloads[file_id] = n` and `fail_moves[file_id] = n` make the next n calls raise `FakeDriveError`;
- `downloads` and `moves` record the successful calls;
- `move` removes the file from every folder.

## Real OAuth setup (owner, once)
1. In Google Cloud Console, enable the Drive API and create an OAuth client of type "Desktop app". Download its JSON somewhere outside git.
2. Set `GOOGLE_OAUTH_CLIENT_JSON=/path/to/client.json` in `.env`, then run `ace init` and `ace auth google`. A browser opens on a localhost port. The token is saved to `data/secrets/google_token.json` with mode 0600.
3. Set `drive.footage_inbox_folder_id` and `footage_processed_folder_id` in `config/local.toml`. The id is the last part of the folder's URL.
4. If the consent screen is in "Testing" mode, refresh tokens expire after 7 days. Publish the app (Production) to avoid that.

## Gotchas
- With no saved `expiry`, google-auth treats the token as expired and refreshes over the network. Unit tests must set a future expiry or pass a fake `request`. `tests/unit/test_drive_auth.py` patches `engine.drive.google.Request` so it can't touch the network.
- The retries are the library's `num_retries` (randomized `2**n` backoff), not `drive.backoff_base_s`, which is unused. Tests patch `googleapiclient.http.time.sleep`.
- Downloads write to `<dest>.part` and rename it when complete. A failed download leaves no file behind. A new run restarts the download from byte 0; it doesn't resume across runs.
- Client tests use the real `googleapiclient` over `HttpMockSequence` with `build(..., static_discovery=True)`, so they work offline.
- google-auth's `to_json`, `from_authorized_user_file` and `refresh` are untyped. Each has a `# type: ignore[no-untyped-call]`.
- A scratch file isn't deleted when its row ends `failed`; it stays for debugging.

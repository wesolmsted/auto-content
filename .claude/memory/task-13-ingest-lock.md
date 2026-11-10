---
name: task-13-ingest-lock
description: Footage ingest commits after every state change so other processes can write during downloads/builds/moves; where the commits are, how the lock test works, gotchas
type: project
---

`ace ingest footage` used to hold a SQLite write lock during Drive downloads, so `ace snippets add` in another process failed with `database is locked` (sqlite3 waits only 5 s).

**Why:** `poll_footage` flushed its inserted `new` rows (`_insert` calls `session.flush()`, and later queries autoflush), which opens the write transaction. The downloads and the duplicate moves then ran inside it, and the job only committed after `poll_footage` returned. The build step was already committed per file and never held the lock. **How to apply:** in any job that does slow work (network, ffmpeg, vision calls), commit before the slow call. Don't leave flushed rows pending across it.

## Commit points now in place (`engine/ingest/poller.py`, `engine/ingest/job.py`)
- `_record_listing`: after each insert, and after each duplicate move attempt.
- `_download_new`: after each download, whether it succeeded or failed.
- `complete_tagged_footage`: after each move, whether it succeeded or failed (it runs at the start of the poll and at the end of the job).
- `run_footage_ingest` build loop: after each file, as before.
- `poll_footage` now commits itself. Its docstring no longer says "the caller commits". The job's separate commits after the poll and after the completion step were removed as redundant.
- The state machine and the attempts/failure semantics are unchanged.

## How the test reproduces the lock (`tests/integration/test_ingest_job_lock.py`)
- It uses the file-based `db_url` fixture (a SQLite file in `tmp_path`) with the job's own session.
- A second engine opens the same file with `connect_args={"timeout": 0.2}`. `OtherProcess.insert_song` inserts a `SongRow` and records `"<step>: ok"` or `"<step>: database is locked"`. It catches the error and doesn't raise, so the job can't swallow it as a step failure.
- `WritingDriveClient` (subclasses `FakeDriveClient`) calls it in `download` and `move`. `WritingShotBuilder` calls it during the build.
- The test asserts the exact list of outcomes.
- A third test simulates a crash: a `KeyboardInterrupt` on the second download. It checks that the first file's `downloaded` state was committed.

## Gotchas
- Before the fix, only the download and duplicate-move outcomes failed. The build and tagged-move outcomes passed on the old code and stay as regression guards.
- pysqlite doesn't BEGIN for SELECTs, and the queries are consumed with `list(...)`. That's why reads between commits don't block other writers. Only flushed, uncommitted DML holds the lock.
- Run: `uv run pytest tests/integration/test_ingest_job_lock.py tests/integration/test_ingest_job_footage.py tests/unit/test_ingest_poller_footage.py`.

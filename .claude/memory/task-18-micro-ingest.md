---
name: task-18-micro-ingest
description: Slice 18 micro ingest: parse_sidecar and poll_micro_sets syncing live `_micro` Drive folders into micro_sets/micro_sources; state rules beyond DESIGN §7.10, gotchas, how to test
type: project
---

Slice 18 built `ace micro ingest`'s engine: sidecar parsing and the live-folder sync (DESIGN.md §7.10 Input and Ingest).

**Why:** slice 19's CLI/job calls this, and the schedule relies on the statuses it sets. **How to apply:** call `poll_micro_sets` as it is. Its behavior in the cases the spec leaves open is listed below.

## Public API
- `engine/micro/sidecar.py`: `parse_sidecar(text) -> Sidecar`, `SidecarError(ValueError)`.
  - Strips a BOM, splits on any newline and ignores blank lines.
  - The first line must start with `song:` (any case). Other lines are text options, stripped.
  - Messages: `sidecar is empty`, ``first non-empty line must be `song: <name>`, got '...'``, `song name is empty`, `sidecar has no text options after the song line`.
- `engine/micro/ingest.py`: `poll_micro_sets(session, drive, store, drive_settings, micro_settings, *, ffprobe="ffprobe") -> MicroIngestSummary`.
  - Commits itself after every change. Moves nothing in Drive.

## Behavior the spec leaves open
- **Summary counts:**
  - `sets`: the `_micro` folders listed this run.
  - `waiting_sets`: listed sets still `waiting` after the run.
  - The other counts are transitions made during this run.
- **Restored folder:** goes back to `active` if it ever had valid texts, otherwise to `waiting`. A renamed folder (same id, still `_micro`) just updates `name`.
- **Removed set:** its videos are left untouched (status and local files kept), so restoring the folder downloads nothing.
- **The `.txt`:**
  - Any file whose name ends in `.txt` (any case) counts, whatever its mime type.
  - Error messages: `no .txt file in the folder`, `more than one .txt file: A.txt, b.txt` (sorted by name), `SidecarError: ...`, `SidecarError: sidecar is not UTF-8 text`, and `<ExceptionType>: ...` when the download fails.
  - Sidecar problems never count attempts, since sets have no attempts column.
  - An invalid `.txt` is not remembered (`sidecar_md5` keeps the last good one), so it is re-downloaded every run until it's fixed. When a single unchanged `.txt` is left (say, the extra one was deleted), the error is cleared without a download.
  - The scratch copy (`scratch/micro_set_<id>.txt`) is deleted right after parsing.
- **Duplicates:** checked only when a row is inserted, reappears or changes md5, against any other source not `removed` (`failed` and `duplicate` count too). They are not re-checked later. If the original is removed, the duplicate stays `duplicate` until its file changes or reappears.
- **Reappeared video, or changed md5** (any status, `failed` included, so re-uploading a too-short video retries it): the local file is deleted, media fields are cleared and attempts reset, then the video becomes `new`, or `duplicate` if its md5 matches. Missing videos are removed before new ones are classified, so swapping a file for a re-upload of the same content isn't treated as a duplicate.
- **Moved video:** one moved between two live sets (same Drive id) gets its `set_id` updated.
- **Local files:** a too-short video keeps its local file and `local_path` (`failed`, no attempts). A failed download or probe deletes the partial file, and `local_path` stays None.

## Gotchas and testing
- `tests/integration/test_micro_ingest.py` uses `FakeDriveClient` with the bytes of the `landscape_motion_clip` (6 s, audio) and `portrait_scenes_clip` (10 s, silent) fixtures, because the probe is real ffprobe. The too-short case sets `min_source_s` to 7.0.
- `drive.remove(folder_id)` removes the folder from the inbox but keeps its files. `drive.add_folder` on the same id restores it with them.
- Run: `uv run pytest tests/unit/test_micro_sidecar.py tests/integration/test_micro_ingest.py`.

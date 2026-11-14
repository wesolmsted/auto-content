---
name: task-16-micro-foundation
description: Slice 16 micro foundation: micro_sets/sources/renders tables, [micro] config, DriveClient.list_files/list_folders, engine/micro contracts and stubs that slices 17-20 fill in
type: project
---

Slice 16 laid the shared pieces for micro edits (DESIGN.md §7.10, D12), so slices 17–20 only fill in their own files.

**Why:** micro work runs as parallel agents; they must not touch shared files. **How to apply:** use these contracts as they are; ask the main agent for any change to them.

## Input model (owner decision, 2026-11-14)
- A micro set is a folder in the footage inbox whose name ends in `_micro`. It holds several finished videos and one `.txt` (`song: <name>` line, then text options).
- The folder stays live in the inbox: each run re-reads it, and nothing is moved in Drive.
- A first draft (single `*_micro` files with a same-name sidecar) was replaced before any database applied migration 0003.

## Shared pieces
- **DB** (`engine/db/models.py`, migration 0003), with columns as in DESIGN §5:
  - `MicroSetRow` (`micro_sets`): one per folder. It holds the song, the texts, `next_text_index`, the sidecar id and md5, and `last_used_at`.
  - `MicroSourceRow` (`micro_sources`): one per video, with `set_id` and `last_used_at`.
  - `MicroRenderRow` (`micro_renders`): `source_id` points at the video.
  - Enums:
    - `MicroSetStatus`: waiting, active, removed.
    - `MicroSourceStatus`: new, ready, duplicate, removed, failed.
    - `MicroRenderStatus`: rendering, ready, downloaded, superseded, failed.
- **Config:** `settings.micro` (`MicroSettings`).
  - `post_times` is a sorted tuple of distinct `datetime.time`, local time in `notify.timezone`.
  - Ranges (`trim_s`, `speed_change`, `zoom`, `contrast`, `saturation`) are `(low, high)` tuples, validated low ≤ high.
  - The other keys are scalars.
- **Media:** `MediaKind.MICRO` (`media/micro/`) holds the source videos. Micro render files go in `MediaKind.RENDERS` as `micro_<id>.mp4`.
- **Drive:** `DriveClient.list_files(folder_id)` returns every non-folder file. `list_folders(folder_id)` returns the subfolders.
  - `FakeDriveClient` helpers:
    - `add_folder(parent, folder_id, name)` nests a folder;
    - `add_file(folder, id, content, name=..., mime_type="text/plain")` adds a `.txt`;
    - `remove(file_id)` and `replace_content(file_id, content)` (new md5) imitate the owner editing Drive.
- **Naming:** `engine/micro/naming.py` `is_micro_folder_name(name)`. The footage poller needs no filter, because `list_videos` never returns folders.

## Contracts (engine/micro/models.py)
- `Sidecar(song_title, text_options)`
- `MicroTextPosition` (top, center)
- `MicroEdit` (its docstring explains each field)
- `MicroSourceMedia(path, duration_s, has_audio)`
- `MicroIngestSummary`: `sets`, `new_sets`, `waiting_sets`, `removed_sets`, `new_videos`, `ready_videos`, `duplicate_videos`, `removed_videos`, `failed_videos`

## Stubs and their owners
- **Slice 17:** `edits.py` and `render.py`.
- **Slice 18:** `sidecar.py` (`parse_sidecar`, `SidecarError`) and `ingest.py` (`poll_micro_sets(...)`).
- **Slice 19:**
  - `cli.py` (`ace micro ingest|render|notify|run|list`; there is no `retire`);
  - new modules `schedule.py`, `job.py` and `notify.py`;
  - the CLI stubs listed in `tests/integration/test_cli.py::test_unbuilt_commands_exist_and_exit_1`, to be removed as they're built.
- **Slice 20:** hand-off routes for `/micro/{id}`.

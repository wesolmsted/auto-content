---
name: task-16-micro-foundation
description: Slice 16 micro foundation: tables, [micro] config, DriveClient.list_files, footage poller skip, engine/micro contracts and stubs that slices 17-20 fill in
type: project
---

Slice 16 laid the shared pieces for micro edits (DESIGN.md §7.10, D12), so slices 17–20 only fill in their own files.

**Why:** micro work runs as parallel agents; they must not touch shared files. **How to apply:** use these contracts as-is; ask the main agent for any change to them.

## Shared pieces
- DB (`engine/db/models.py`, migration 0003): `MicroSourceRow` (`micro_sources`) and `MicroRenderRow` (`micro_renders`), columns as in DESIGN §5. Enums `MicroSourceStatus` (new, downloaded, ready, duplicate, retired, failed) and `MicroRenderStatus` (rendering, ready, downloaded, superseded, failed).
- Config: `settings.micro` (`MicroSettings`). `post_times` is a sorted tuple of distinct `datetime.time` (local time in `notify.timezone`). Ranges (`trim_s`, `speed_change`, `zoom`, `contrast`, `saturation`) are `(low, high)` tuples, validated low ≤ high. Scalars: `brightness` and `hue_degrees` (±), `mirror_probability`, `text_size_fraction`, `text_size_jitter` (± fraction of the size), `text_y_jitter` (± fraction of frame height), `text_max_chars`, `late_grace_minutes`, `expire_hours`, `max_attempts`, `min_source_s`.
- `MediaKind.MICRO` (`media/micro/`): where micro sources are kept. Micro render files go in `MediaKind.RENDERS` as `micro_<id>.mp4`.
- `DriveClient.list_files(folder_id)`: every non-folder file (sidecars included). `FakeDriveClient.list_files` returns everything in the folder; use `add_file(..., name="x_micro.txt", mime_type="text/plain")` for sidecars.
- `engine/micro/naming.py`: `is_micro_video_name(name)`, `sidecar_name_for(video_name)`. The footage poller drops `_micro` videos from its listing.

## Contracts (engine/micro/models.py)
`Sidecar(song_title, text_options)`, `MicroTextPosition` (top, center), `MicroEdit` (see docstring for field meanings), `MicroSourceMedia(path, duration_s, has_audio)`, `MicroIngestSummary`.

## Stubs and their owners
- `edits.py` `sample_edit(rng, settings, text)`, `output_duration_s(edit, source_duration_s)`; `render.py` `render_micro(edit, source, out_path, settings) -> argv` (renders and validates), `MicroRenderValidationError`: slice 17.
- `sidecar.py` `parse_sidecar(text)`, `SidecarError`; `ingest.py` `poll_micro_sources(...)`: slice 18.
- `cli.py` (all `ace micro` commands) plus new `schedule.py`, `job.py`, `notify.py`: slice 19. The CLI stubs are listed in `tests/integration/test_cli.py::test_unbuilt_commands_exist_and_exit_1`; remove them as you build them.
- Hand-off routes and templates for `/micro/{id}`: slice 20.

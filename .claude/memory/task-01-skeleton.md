---
name: task-01-skeleton
description: Map of the slice-1 skeleton for parallel agents: shared files, contracts, stub signatures, fixtures with ground truth, how to run checks, gotchas
type: project
---

Slice 1 created every shared file up front, so slices 2–10 only fill in their own module.

**Why:** parallel agents must not touch the same files. **How to apply:** edit only your module's files (including `engine/<module>/cli.py` and `tests/fakes/<module>.py`). If you need a shared file changed, say so in your report.

## Shared files (don't edit; ask the main agent)
- `pyproject.toml`, `uv.lock`, `.python-version`, `Makefile`, `Dockerfile`, `.github/workflows/ci.yml`
- `engine/config.py`, `config/default.toml`, `.env.example`
- `engine/db/` (`enums.py`, `types.py`, `models.py`, `session.py`, `migrate.py`, `repository.py`), `alembic/`, `alembic.ini`
- `engine/cli.py`, `engine/cli_common.py`
- Contract models: `engine/{drive,ingest,snippets,recipes,notify,measure}/models.py`
- Protocols: `engine/drive/client.py`, `engine/vision/tagger.py`, `engine/notify/base.py`, `engine/measure/readers.py`, `engine/storage/media_store.py`
- Shared helpers: `engine/ffmpeg.py`, `engine/http.py`, `engine/log.py`
- `tests/conftest.py`, `tests/fixtures/make_fixtures.py`, `tests/unit/test_no_publish_guard.py`

## Config (`engine/config.py`)
- `load_settings(config_dir=None, env_file=Path(".env")) -> Settings`. Precedence: env > `.env` > `config/local.toml` > `config/default.toml`. `config_dir` defaults to `$ACE_CONFIG_DIR` or `./config`. In tests, pass `env_file=None`.
- `default.toml` is the only place defaults live: every section key is required there, with no Python defaults. In unit tests, get a section from `load_settings(<repo>/config, env_file=None).<section>`, then `.model_copy(update=...)`.
- Env overrides: `ACE_<SECTION>__<KEY>` (e.g. `ACE_RENDER__FPS=24`), `ACE_DATA_DIR`. Secrets use their plain names (`ANTHROPIC_API_KEY`, `NTFY_TOPIC`, …) and are flat fields on `Settings`; the secret ones are `SecretStr`. Empty env values are ignored.
- Sections: `drive` (folder ids default None), `ingest`, `vision` (+`vocabulary`), `snippets`, `recipes` (`space`, `fixed`: 3 `Recipe`s), `render`, `tools` (ffmpeg/ffprobe binary names), `captions.templates[Platform]`, `cadence`, `handoff`, `notify` (+`quiet_hours`, `timezone`), `match`, `measure`, `http`, `learn`.
- Derived: `settings.db_url`, `db_path` (`data_dir/db/engine.sqlite3`), `media_dir`, `secrets_dir`.

## DB (`engine/db/`)
- ORM classes all end in `Row`: `FootageFileRow`, `ShotRow`, `SongRow`, `SnippetRow`, `RecipeRow`, `RenderRow`, `PostRow`, `MetricRow`, `MetricImportRow`, `VariableScoreRow`, `StyleReferenceRow`. Convert to and from the domain models in your module.
- Enums in `engine/db/enums.py`: `FootageStatus` (adds `duplicate`), `RenderStatus`, `PostStatus`, `MetricImportStatus`, `StyleReferenceStatus`, `Platform` (`ig_trial`, `tiktok`), `MatchMethod`, `MetricCheckpoint` (`24h`, `72h`, `export`), `MetricSource`, `Orientation`, `SnippetType`, `RejectReason`; plus `INTERNAL_SCORE_PLATFORM = "internal"`.
- Enum columns are stored as plain strings, so a new member needs no migration; unknown values are rejected on write. Datetimes must be timezone-aware: naive ones are rejected, and reads come back as UTC.
- `create_db_engine(url)` turns on SQLite foreign keys. Also `make_session_factory(engine)`, `session_scope(factory)` (commit or rollback), `upgrade_to_head(url)` and `table_counts(session)`.
- Columns added beyond DESIGN §5: `shots.proxy_path`, `renders.ffmpeg_argv_json`, and `updated_at` on `renders`, `posts` and `style_references`.

## Contract models
- `DriveFile(id, name, mime_type, md5, size_bytes, created_at, description)`
- `ShotTags(category, subject, setting, lighting, mood, has_face, quality 0..1, issues: tuple[QualityIssue])`. The string values come from `vision.vocabulary`.
- `Shot(id|None, footage_file_id, start_s, end_s, width, height, fps, orientation, motion_score, quality_score, tags, proxy_path, thumbnail_path, use_count, last_used_at)`, with property `duration_s`. Also `PollSummary`.
- `SongAnalysis(duration_s, bpm, beats, downbeats)`; `BeatGrid(bpm, beats, downbeats, drop_s|None, duration_s)`. Grid times are relative to the snippet start. Both are validated: beats strictly ascending within [0, duration], and downbeats a subset of beats.
- `Recipe`: enum fields (`CutRate`, `OpeningShot`, `FootageCategory`, `OnScreenText`, `SnippetType`, `Look`, `Effect`, `TextStyle`), plus `hard_cut_on_drop`, `length_s`, `effects` (a sorted set), and `source` (not part of the hash). `.id` is `"rcp_" + sha256(sorted compact JSON of canonical_params())[:12]`.
- `Timeline(duration_s, look, segments, effects, texts)`; property `shot_ids`. Validators: segments are contiguous from 0 to `duration_s`, and events end within the duration. `Segment(shot_id, source_path, source_in_s, source_out_s, output_start_s, output_end_s, speed)` requires source span = output span × speed. Also `EffectEvent(effect, start_s, duration_s)`, `TextEvent(text, start_s, end_s, style)`, and `RenderPlan(recipe_id, snippet_id, timeline, targets, captions)`.
- `Notification(title, body, click_url, tags)`
- `PlatformVideo(platform, external_id, permalink, posted_at, caption, duration_s)`; `MetricSnapshot(fetched_at, views, reach, likes, comments, shares, saves, avg_watch_s, full_watch_rate, skip_rate, raw)`; `AwaitingPost(post_id, platform, downloaded_at, duration_s, caption)`; `Match(post_id, external_id, permalink, posted_at, score)`.

## Protocols
- `DriveClient`: `list_videos(folder_id)`, `download(file_id, dest) -> Path`, `move(file_id, to_folder_id)`.
- `VisionTagger.tag_shot(frames) -> ShotTags`, raising `VisionTagError`. `describe_reference` is deferred to Phase 3.
- `Notifier.send(notification)`, raising `NotificationError`.
- `PlatformReader`: property `platform`, `recent_videos(since)`, `fetch_metrics(external_id)`. It is read-only.
- `MediaStore.path_for(kind: MediaKind, name) -> Path` returns an absolute path and creates its directory. `LocalMediaStore(media_dir)` also has `ensure_dirs()`. Kinds are `scratch`, `shots`, `renders`; names must be plain file names.

## Stubs to replace (all raise NotImplementedError)
- `ingest/poller.py`: `poll_footage(session, drive, store, settings: DriveSettings, max_attempts) -> PollSummary`. It covers moving tagged files to done, listing, dedup and download.
- `ingest/shots.py`: `build_shots(video_path, footage_file_id, tagger, store, settings: IngestSettings) -> list[Shot]`
- `snippets/analysis.py`: `analyze_song(audio_path, settings) -> SongAnalysis`; `detect_drop(audio_path, song, start_s, end_s, settings) -> float|None` (absolute song time); `beat_grid_for_snippet(song, start_s, end_s, drop_s) -> BeatGrid` (pure).
- `recipes/selection.py`: `select_shots(recipe, library, beat_grid, rng, settings: RecipeSettings, now) -> list[Shot]`
- `recipes/assembly.py`: `assemble(recipe, shots, beat_grid, text=None) -> Timeline`
- `render/ffmpeg.py`: `render(timeline, audio_path, audio_start_s, out_path, settings: RenderSettings) -> list[str]` (returns the argv); `validate(path, expected_duration_s, settings)`, raising `RenderValidationError`.
- `notify/ntfy.py`: `NtfyNotifier(server, topic, token, client: httpx.Client).send`
- `vision/claude.py`: `ClaudeVisionTagger(client: anthropic.Anthropic, settings: VisionSettings).tag_shot`
- `drive/google.py`: `GoogleDriveClient(credentials, settings: DriveSettings)`

## CLI ownership
`engine/cli.py` only mounts the module CLIs.
- `ingest/cli.py`: `ingest footage|references|exports`.
- `snippets/cli.py`: `snippets add|list`.
- `render/cli.py`: `render batch|one`.
- `notify/cli.py`: `notify test`.
- `handoff/cli.py`: `serve`.
- `measure/cli.py`: `measure due`, `posts link` (`posts_app`), and `auth_meta` / `auth_tiktok`.
- `drive/cli.py`: `auth_google`.
- `learn/cli.py`: `learn update` and `report weekly` (`report_app`).

Stubs call `engine.cli_common.not_implemented()`, which prints "not implemented yet" and exits 1. `tests/integration/test_cli.py::test_unbuilt_commands_exist_and_exit_1` lists every stubbed command: remove your command from that list when you implement it.

## Shared helpers
- `engine/ffmpeg.py`: `run_ffmpeg(args, ffmpeg="ffmpeg")` (adds `-hide_banner -nostdin -y`), `run_tool(argv)`, `probe(path) -> MediaInfo(duration_s, width, height, fps, rotation, has_audio)`, and `FfmpegError(argv, returncode, stderr_tail)`.
- `engine/http.py`: `request_with_retries(client, method, url, *, max_retries, backoff_base_s, sleep=time.sleep, **kw)`. It retries 429/5xx and transport errors, and fails fast on 4xx.
- `engine/log.py`: `configure_logging(level)` outputs JSON lines; pass `extra={"job":..., "row_id":...}`. The `ace` root callback calls it.

## Tests and fixtures
- Run everything with `make check` (ruff, ruff format --check, mypy --strict engine, pytest). Fast loop: `uv run pytest tests/unit -x`. Auto-fix with `make fix`.
- conftest fixtures: `db_url` (fresh migrated SQLite), `db_session`. Session-scoped media, generated by `tests/fixtures/make_fixtures.py` (import its constants as ground truth):
  - `portrait_scenes_clip`: 1080x1920, 10.0 s, 30 fps, no audio, with hard cuts at exactly 2.5, 5.0 and 7.5 s (frames 75/150/225). PySceneDetect `ContentDetector()` finds them within 1 frame.
  - `landscape_motion_clip`: 1280x720, 6.0 s, 30 fps, testsrc2 motion plus a 440 Hz AAC tone.
  - `click_track_wav`: 44.1 kHz mono, 20.0 s, 120 BPM. There is a click every 0.5 s from t=0, accented on every 4th beat (downbeats at 0, 2, 4, … s). The drop is at 8.0 s (a downbeat): clicks get 2.5x louder and a 60 Hz bed is added, so RMS rises more than 3x.
- Put fakes in `tests/fakes/<module>.py`.

## Gotchas
- The Mac's system python3.12 is an x86_64 (Rosetta) build, and llvmlite (via librosa) has no wheel for it. pyproject sets `python-preference = "only-managed"`, so uv uses its own arm64 3.12. Docker overrides this with `--no-managed-python`.
- `exclude-newer = 2026-10-25` in pyproject: uv only resolves releases older than that date. Bump it deliberately when you need a newer package.
- scenedetect depends on GUI `opencv-python`; pyproject overrides it away so only `opencv-python-headless` provides `cv2`. If `import cv2` ever breaks after a sync, run `uv sync --reinstall-package opencv-python-headless`.
- `alembic/` lives at the repo root, so `engine.db.migrate` needs the checkout (an editable install). The migration uses plain `sa.String`/`sa.DateTime`, not the engine's types. New migrations: `ACE_DATA_DIR=<tmp> uv run alembic revision --autogenerate -m "..."`, then replace any `engine.db.types.*` with `sa.String(32)` / `sa.DateTime(timezone=True)`. `test_migrations_match_the_models_exactly` catches drift.
- ruff must not touch Markdown (`extend-exclude = ["*.md"]`): it would reformat code blocks in DESIGN.md.
- librosa `beat_track` on the click track gives ~117.5 BPM and skips the first ~2 s of beats. The beat/drop code in slice 4 must handle this, or seed the tempo from config.
- The no-publish guard scans `engine/**/*.py` text for `media_publish`, `/post/publish`, `upload-post`, and `.post(...)` or `request("POST", ...)` to a URL ending in `/media`. A GET to `/{ig-user-id}/media` is fine.

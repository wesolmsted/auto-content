# Auto Content Engine — Design Document

> Status: **draft v0.2** (2026-11-08). This is the engineering reference for all coding work. The [README](../README.md) is the product spec: *what* and *why*. This document is the *how*: architecture, module contracts, data model, conventions and decisions. When code and this document disagree, fix one of them in the same change.

---

## 1. Decisions locked in

| # | Decision | Choice | Consequence |
| --- | --- | --- | --- |
| D1 | Runtime | **Local first, cloud-ready** | Runs on the Mac via launchd. All paths, credentials and storage go through config, and the app ships with a Dockerfile, so it can move to a scheduled cloud job without code changes. |
| D2 | Review UI | **Minimal hand-off web page** | FastAPI + server-rendered HTML. Reached from the phone over Tailscale (no public exposure). |
| D3 | Drive auth | **OAuth on the owner's Google account** | One-time browser consent, refresh token stored in the secrets dir. Scope: `drive`. |
| D8 | Posting | **Manual hand-off; the system never posts** | When a render is ready, the owner gets a phone notification. The hand-off page has the video and caption; the owner saves the video and posts it from the Instagram app as a Trial Reel. No platform posting API is used. Supersedes D4/D4b. |
| D9 | Approval | **Downloading the video = approval** | There is no separate approve step. A download from the hand-off page moves the render to `approved`. |
| D10 | Phone notifications | **ntfy** (default; behind `Notifier`) | One HTTP POST per hand-off with a tap-through link. Swappable for Pushover/Telegram. |
| D5 | Language | Python 3.12 | Best fit for FFmpeg, librosa, PySceneDetect, OpenCV. |
| D6 | Database | SQLite via SQLAlchemy 2.0 + Alembic | Single file locally; same models move to Postgres in the cloud. |
| D7 | Orchestration | DB-driven idempotent jobs, triggered by CLI commands on a schedule | No queue broker. Each run picks up whatever work the DB says is due. |
| D11 | Platforms | **Instagram Trial Reels only** | Every render targets `ig_trial`, and only the Meta Graph API is read. The platform plumbing (`Platform` enum, per-target captions and posts, `PlatformReader`, scores keyed by platform) stays, with one value. |
| D12 | Micro edits | **`<song>_micro` folders of finished reels get small random edits, kept fully separate from recipe renders** | Finished reels are reposted as Trial Reels three times a day. A folder holds several videos and one `.txt` with the song name and on-screen texts. Each copy gets fresh small edits (trim, speed, zoom, mirror, color, on-screen text) so it isn't a duplicate. Micro renders have their own tables, schedule and hand-off section, and no posts, metrics or learning (§7.10). |

New decisions get appended to the decision log in §13.

---

## 2. Design principles

1. **The database is the source of truth for pipeline state.** Every unit of work (a footage file, a render, a post, a metric checkpoint) has a row with a `status`. Jobs query for rows in a given state, do the work, and advance the state. A crash mid-run loses nothing; the next run resumes.
2. **Every job is idempotent and re-runnable.** Running `ace ingest` twice in a row does nothing the second time. Dedup keys are explicit (Drive `fileId` + `md5Checksum`, recipe param hash, etc.).
3. **Pure core, thin edges.** Decisions (shot selection, beat-grid cutting, post matching, bandit sampling, scoring) are pure functions over plain data and are unit-tested without I/O. External systems (Drive, vision API, FFmpeg, notifications, Meta) sit behind small interfaces with fakes for tests.
4. **Plan, then execute.** Rendering is split into building a `Timeline` (pure data: which shot, which in/out points, which effects, at which output times) and executing it with FFmpeg. The timeline is stored with the render, so any video can be explained and reproduced.
5. **One failure doesn't block the batch.** Per-item errors are recorded on the row (`error`, `attempts`) and the job moves on. Items past `max_attempts` go to `failed` and show up in the report.
6. **The system never posts.** No code calls a platform's publish, upload or draft endpoint. Platform APIs are used **read-only**, for metrics. Every post goes live only when the owner publishes it from the platform's app. This is a hard rule: don't add a posting client, even behind a flag.
7. **No secrets or personal paths in code or git.** Everything environment-specific comes from config.

---

## 3. System overview

```mermaid
flowchart TB
    subgraph Scheduled CLI jobs
        ING[ace ingest footage]
        REF[ace ingest references]
        GEN[ace render batch]
        MEA[ace measure due]
        LRN[ace learn update]
        RPT[ace report weekly]
    end
    WEB[ace serve<br/>hand-off web app]

    DB[(SQLite)]
    FS[(Local media store<br/>scratch / shots / renders)]

    DRIVE[Google Drive] --> ING & REF
    VISION[Vision API] --> ING & REF
    ING & REF & GEN & MEA & LRN & RPT & WEB --> DB
    ING & GEN --> FS
    WEB --> FS
    GEN --> NTFY[ntfy → phone]
    PHONE([Owner's phone]) -->|Tailscale| WEB
    PHONE -->|posts manually| APPS[Instagram app]
    MEA -->|read-only| META[Meta Graph API]
```

Two long-lived processes at most: the scheduler (launchd locally, cron/cloud scheduler later) that invokes CLI jobs, and `ace serve` for the hand-off page. Everything else is a short-lived command.

### Default schedule

| Job | Cadence | Notes |
| --- | --- | --- |
| `ace ingest footage` | every 30 min | Poll, download, split, tag. |
| `ace ingest references` | every 30 min | Phase 3. |
| `ace render batch` | daily, 06:00 | Renders N candidates (config: `cadence.renders_per_day`, default 4). Their notifications are held until the 4–7 pm ET posting window (§7.5). |
| `ace notify pending` | every 15 min, 16:00–19:00 ET | Sends the held hand-off notifications. Morning batch renders only notify on a run inside the window. |
| `ace measure due` | hourly | Matches newly posted videos to renders, then pulls any due 24 h / 72 h checkpoints. |
| `ace learn update` | daily, after measure | Recomputes variable scores. Phase 2. |
| `ace report weekly` | Mondays 07:00 | Report + shoot list. Phase 2. |
| `ace micro run` | every 15 min | Micro edits (§7.10): re-read the `_micro` folders, render today's slots, send due notifications. |

---

## 4. Repository layout

The README's proposed layout, made concrete. The installable package is `engine`; the CLI entry point is `ace`.

```
auto-content/
├── README.md                  # product spec
├── CLAUDE.md                  # instructions for coding sessions
├── docs/
│   ├── DESIGN.md              # this document
│   └── runbooks/              # setup: Google OAuth, Meta app (read-only), ntfy, Tailscale
├── pyproject.toml             # deps, ruff, mypy, pytest config; `ace` console script
├── uv.lock
├── Dockerfile                 # cloud-ready image (python + ffmpeg)
├── .env.example               # every env var, no values
├── config/
│   ├── default.toml           # cadence, recipe space, thresholds (no secrets)
│   └── luts/                  # .cube files for `look`
├── alembic/                   # migrations
├── engine/
│   ├── cli.py                 # Typer app; one subcommand per job
│   ├── config.py              # Settings (pydantic-settings): TOML + env
│   ├── db/                    # SQLAlchemy models, session, repositories
│   ├── storage/               # MediaStore (local now, bucket later)
│   ├── ffmpeg.py              # shared ffmpeg/ffprobe subprocess wrapper
│   ├── http.py                # shared HTTP client with retries/backoff
│   ├── log.py                 # JSON logging setup
│   ├── drive/                 # DriveClient: list / download / move
│   ├── vision/                # VisionTagger interface + Claude implementation, prompts
│   ├── ingest/                # footage poller, scene split, shot tagging
│   ├── snippets/              # beat / downbeat / drop analysis
│   ├── recipes/               # Recipe model, recipe space, shot selection, timeline assembly
│   ├── render/                # Timeline → FFmpeg command, LUTs, text, effects
│   ├── references/            # reference analysis → style card → recipe + notes
│   ├── handoff/               # FastAPI hand-off app, templates, download = approval
│   ├── notify/                # Notifier interface + ntfy implementation
│   ├── measure/               # PlatformReader interface (Meta), post matching, metric pulls
│   └── learn/                 # scoring, Thompson sampling, report, shoot list
├── data/                      # gitignored: sqlite db, media store, secrets
├── scripts/                   # one-off dev helpers only (jobs belong in the CLI)
└── tests/
    ├── unit/                  # pure logic, no I/O
    ├── integration/           # real ffmpeg / sqlite against tiny fixtures
    ├── fakes/                 # in-memory Protocol implementations
    └── fixtures/              # generated test clips + audio (see §11)
```

Dependency direction: `cli` → feature modules (`ingest`, `recipes`, …) → shared infrastructure (`db`, `storage`, `drive`, `vision`, `notify`, `config`). Feature modules don't import each other's internals; they communicate through the DB and the per-module contract models (`engine/<module>/models.py`).

Each module owns its CLI commands in `engine/<module>/cli.py`, which `engine/cli.py` mounts. `ace auth google` lives in `drive/cli.py`, `ace auth meta` and `ace posts link` in `measure/cli.py`, and `ace report weekly` in `learn/cli.py`.

---

## 5. Data model

Refinements over the README's first pass:

- `references` is a reserved word in SQL → table is **`style_references`**.
- Songs and snippets are split, so a song's audio is analyzed once and snippets are ranges on it.
- Every processed item carries `status`, `error`, `attempts`, `updated_at` for the job pattern in §2.
- Recipes are identified by a **hash of their canonical params**, so the same style choices always map to the same recipe row.
- A render has **target platforms** (only `ig_trial`, D11) and a caption per target. A `posts` row per target is created at approval (download) and later **matched** to the video the owner actually posted.
- Metrics keep the raw API payload, so new signals can be derived later without re-fetching.
- Variable scores are keyed by platform (`ig_trial`, plus `internal` for the taste prior).

```
footage_files
  id PK, drive_file_id UNIQUE, md5, name, size_bytes, local_path,
  status [new|downloaded|split|tagged|done|duplicate|failed], error, attempts,
  created_at, updated_at

shots
  id PK, footage_file_id FK, start_s, end_s, duration_s,
  width, height, fps, orientation [portrait|landscape|square],
  category, subject, setting, lighting, mood,          -- from vision tags
  motion_score REAL, quality_score REAL,                -- 0..1
  has_face BOOL, tags_json, thumbnail_path, proxy_path,
  use_count INT DEFAULT 0, last_used_at,
  created_at

songs
  id PK, title, audio_path, duration_s, bpm, beats_json, downbeats_json,
  analyzed_at

snippets
  id PK, song_id FK, label, snippet_type [hook|drop|intro_build],
  start_s, end_s, drop_s NULL, active BOOL, created_at

recipes
  id PK (e.g. "rcp_" + first 12 hex of sha256(canonical params)),
  params_json, source ["generated" | "reference:<id>"], active BOOL, created_at

renders
  id PK, recipe_id FK, snippet_id FK, timeline_json, shot_ids_json,
  file_path, duration_s, targets_json,                  -- ["ig_trial"]
  captions_json,                                        -- {platform: caption}
  status [rendering|ready|approved|rejected|superseded|failed],
  notified_at, downloaded_at, download_count,
  reject_reason NULL, reject_note NULL, reviewed_at,
  ffmpeg_argv_json, error, attempts, created_at, updated_at

posts
  id PK, render_id FK, platform [ig_trial],
  status [awaiting_post|matched|not_posted],
  external_id NULL, permalink NULL, posted_at NULL,
  match_method [auto|manual] NULL, match_score REAL NULL,
  created_at, updated_at, UNIQUE(render_id, platform)

metrics
  id PK, post_id FK, checkpoint [24h|72h], source [api], fetched_at,
  views, reach, likes, comments, shares, saves,
  avg_watch_s, full_watch_rate, skip_rate, raw_json,
  UNIQUE(post_id, checkpoint)

variable_scores
  variable, value, platform, alpha REAL, beta REAL, n_posts INT, updated_at,
  PK(variable, value, platform)

style_references
  id PK, drive_file_id UNIQUE, md5, name, note, source_link,
  status [new|downloaded|analyzed|failed], error, attempts,
  style_card_json, recipe_id FK NULL, recreation_notes, created_at, updated_at

micro_sets                                              -- §7.10: one `<song>_micro` Drive folder
  id PK, drive_folder_id UNIQUE, name, song_title NULL, caption NULL, text_cutoff_s NULL,
  text_options_json,
  next_text_index INT DEFAULT 0, sidecar_drive_file_id NULL, sidecar_md5 NULL,
  status [waiting|active|removed], error, last_used_at NULL, created_at, updated_at

micro_sources                                           -- one video in a micro set
  id PK, set_id FK, drive_file_id UNIQUE, md5, name,
  local_path NULL, duration_s NULL, has_audio BOOL NULL,
  status [new|ready|duplicate|removed|failed], error, attempts,
  last_used_at NULL, created_at, updated_at

micro_renders                                           -- §7.10; no posts, metrics or scores
  id PK, source_id FK, scheduled_for, edit_json, text, caption,
  file_path NULL, duration_s NULL,
  status [rendering|ready|downloaded|superseded|failed],
  notified_at, downloaded_at, download_count,
  ffmpeg_argv_json, error, attempts, created_at, updated_at
```

Schema changes go through Alembic migrations, never hand edits to the DB. The SQLite file is `data_dir/db/engine.sqlite3`. Enums are stored as plain strings, so adding a value needs no migration. Datetime columns accept only timezone-aware UTC values.

### State machines

```mermaid
stateDiagram-v2
    direction LR
    state "footage_files" as F {
        [*] --> new
        new --> downloaded
        downloaded --> tagged: build_shots splits + tags in one step
        tagged --> done: moved to Processed in Drive
    }
```

**Render**

```mermaid
stateDiagram-v2
    direction LR
    [*] --> rendering
    rendering --> ready: file validated + notification sent
    ready --> approved: owner downloads video
    ready --> rejected: owner taps Reject
    ready --> superseded: not downloaded within handoff.expire_hours of notified_at
    approved --> [*]: posts rows created (awaiting_post)
```

**Post**

```mermaid
stateDiagram-v2
    direction LR
    [*] --> awaiting_post
    awaiting_post --> matched: found on platform (auto or manual link)
    awaiting_post --> not_posted: no match within match.window_hours
    not_posted --> matched: manual link later
```

Any processing state can move to `failed` once `attempts >= max_attempts`. The local download is deleted once a footage file reaches `done`; shots are re-cut from Drive if ever needed (scratch is disposable). Render files are kept until every post for that render is `matched` or `not_posted` + `renders.keep_days`.

---

## 6. Module contracts

Interfaces are `typing.Protocol`s. Each has a real implementation and an in-memory fake under `tests/fakes/`.

```python
class DriveClient(Protocol):
    def list_videos(self, folder_id: str) -> list[DriveFile]: ...
    def download(self, file_id: str, dest: Path) -> Path: ...          # resumable, retries
    def move(self, file_id: str, to_folder_id: str) -> None: ...

class VisionTagger(Protocol):
    def tag_shot(self, frames: list[Path]) -> ShotTags: ...            # 3 frames: start/mid/end
    def describe_reference(self, frames: list[Path], note: str | None) -> ReferenceDescription: ...

class MediaStore(Protocol):                                             # local disk now, bucket later
    def path_for(self, kind: str, name: str) -> Path: ...

class Notifier(Protocol):
    def send(self, notification: Notification) -> None: ...            # title, body, click_url, tags

class PlatformReader(Protocol):                                         # read-only; no posting methods
    platform: Platform
    def recent_videos(self, since: datetime) -> list[PlatformVideo]: ...   # id, permalink, posted_at, caption, duration_s
    def fetch_metrics(self, external_id: str) -> MetricSnapshot: ...
```

Domain models (`DriveFile`, `ShotTags`, `Recipe`, `Timeline`, `Notification`, `PlatformVideo`, `MetricSnapshot`, …) are Pydantic v2 models. Vision API responses are parsed into these models and rejected if they don't validate; the shot is retried, never stored half-tagged.

`PlatformReader` deliberately has no write methods (principle 6).

---

## 7. Pipeline stages

### 7.1 Ingest (Phase 1)
1. `list_videos(inbox_folder)`; insert unseen `drive_file_id`s as `new`. A file whose `md5` matches an existing row is marked a duplicate and moved without processing.
2. Download to `data/media/scratch/` (resumable, exponential backoff, one file at a time).
3. Normalize: probe with `ffprobe`; record rotation, fps, resolution.
4. Split with PySceneDetect (`ContentDetector`, threshold in config). Drop shots shorter than `ingest.min_shot_s` (default 0.8 s). Split shots longer than `ingest.max_shot_s` (default 8 s) into sub-shots so selection has granularity.
5. Per shot: extract 3 frames, compute `motion_score` (OpenCV optical flow magnitude, normalized) and a blur/shake heuristic locally, then call `VisionTagger.tag_shot` for semantic tags + quality. Final `quality_score` combines both.
6. Encode a **proxy** for each shot (H.264, already center-cropped to 9:16, width capped at 1080 px) into `data/media/shots/`. Renders only scale the proxy to the output size; the cover-crop in render is then a no-op. Renders read proxies, not originals, so renders don't depend on Drive.
7. Move the Drive file to `Processed`; status `done`.

Vision tag vocabulary (`category`, `lighting`, etc.) is a closed enum defined in `config/default.toml` and included in the prompt, so tags stay matchable against recipe filters.

### 7.2 Snippets (Phase 1)
- `ace snippets add <audio> --start --end --type` (song audio is a local file the owner provides; not in Drive for v1).
- Song analysis with librosa: tempo, beat times, downbeats (librosa beats + bar estimation; swap to madmom if downbeats prove unreliable). Drop detection: largest positive jump in onset-strength/RMS envelope inside the snippet, snapped to the nearest downbeat. The owner can override `drop_s`.
- Outputs a `BeatGrid` (pure data) for the snippet window, times relative to snippet start.
- As built:
  - The beat grid assumes a constant tempo. librosa's beats are refined by a line fit and extended over the whole song; songs with tempo changes would need madmom.
  - Songs are cached by resolved file path.
  - `snippets.drop_s` is stored as absolute song time, like `start_s`/`end_s`.
  - `snippets add` snaps `start_s` to the nearest downbeat before `end_s` (ties go earlier), so videos start on the bar; `end_s` is stored as typed.
  - The drop is auto-detected for every snippet type unless `--drop` is given: the largest RMS rise of at least 3 dB, snapped to a downbeat.

### 7.3 Recipes and assembly (Phase 1)
- `Recipe` is a Pydantic model whose fields are the README's recipe variables, each an enum. The **recipe space** (allowed values per variable) lives in config.
- `select_shots(recipe, library, beat_grid, rng) -> list[Shot]`: filter by orientation (a render never mixes portrait and landscape shots), category and quality floor, exclude shots used in the last `recipes.shot_cooldown_days`, rank by recipe's `opening_shot` criterion for slot 0.
- `assemble(recipe, shots, beat_grid) -> Timeline`: compute cut points from `cut_rate`, force a cut on `drop_s` if `hard_cut_on_drop`, fit total length to `length_s` (snapped to the nearest beat), assign shots to slots (a shot must be at least as long as its slot, or it's skipped for that slot), attach effects keyed to beat/downbeat times, attach text events.
- As built:
  - No cut lands within 0.1 s of another cut or of the ends.
  - Effects are keyed to downbeats, except `shake`, which is on every beat.
  - Segments always start at the proxy's beginning.
  - `plan_batch` writes nothing. The render integration inserts the `recipes` row and updates the shots' `use_count`/`last_used_at`.
  - `plan_batch` picks the recipe first: the one with the fewest non-failed renders in the last 7 days (this batch included), ties broken by `rng`. It then picks a snippet, trying snippets whose drop lands inside the video length before the rest.
  - `one_long_shot` chains the longest shots, longest first, and cuts on the last beat the current shot still covers (plus the drop cut). It ignores `opening_shot`.
  - `ace render one` passes `ignore_limits=True`, skipping the weekly repeat cap and the shot cooldown. `ace render batch` exits 1 if any render failed.
- Both functions are pure and deterministic given a seeded `rng`. These are the most heavily unit-tested functions in the codebase.
- **Targets:** every render targets `ig_trial` (D11).

Phase 1 ships **6 fixed recipes** from config: 3 styles, each in a portrait and a landscape version (`footage_orientation`). Landscape shots are center-cropped to 9:16. Phase 2 replaces fixed recipes with sampling (§7.7).

### 7.4 Render (Phase 1)
- `render(timeline, snippet_audio, out_path)` builds a single FFmpeg filter graph: per-segment `trim` → reframe to 1080×1920 (center crop; face-aware crop is a later improvement) → effects → `concat` → LUT (`lut3d`) → text (`drawtext` with bundled fonts; see "Native-looking text" below) → mux snippet audio → H.264 High / AAC, 30 fps, `+faststart`.
- The exact FFmpeg argv is logged and stored alongside the render for reproducibility.
- As built:
  - Effects are applied after concat, keyed to output time.
  - `execute_plan` retries inside one call, up to `render.max_attempts`, so a render row always ends in `ready` or `failed`.
  - The bundled default font is Anton (SIL OFL 1.1).
  - Text needs an ffmpeg build with `drawtext` (libfreetype). The Homebrew `ffmpeg` formula doesn't include it; `ffmpeg-full` does, and the Docker/Debian image does.
- Output validated with `ffprobe` (duration within ±1 frame of plan, 9:16, has audio).
- Captions per target, templated from config for v1 (open question in §14).
- **Native-looking text** (follow-up task after slice 6): burned-in text should look like text added in the app.
  - **Fonts:** Instagram's in-app font (Instagram Sans) isn't openly licensed, so the preset uses the closest OFL match, and the owner can drop their own font file into `data_dir/fonts/` and point the preset at it.
  - **Styles:** presets copy the in-app looks: plain white with a soft shadow, outline, and a rounded background box (white box with black text, or black with white). Text sits inside the platform safe zones, clear of the caption and button overlays.
  - **Text source:** `ace snippets add --text "<hook lyric>"` stores the hook text on the snippet, and recipes with `on_screen_text = hook_lyric` use it.
- On success: status → `ready`, then send the hand-off notification (§7.5). If the notification fails, the render stays `ready` with `notified_at` NULL, and the next run retries the notification.

### 7.5 Hand-off and approval (Phase 1)

**Notification.** A `Notification` per ready render through `Notifier` (ntfy: `POST https://ntfy.sh/<topic>` or a self-hosted server). Fields: title `"Hand-off ready: <song> · <recipe short name>"`, body with length and targets, `Click` header = the hand-off page URL (Tailscale hostname), tag for the platform icons. The topic name is a long random string kept in secrets (anyone who knows a public ntfy topic can read it). **Posting window:** the owner posts only in prime time, 4–7 pm US Eastern (`America/New_York`, so it follows DST). This is set as quiet hours from 19:00 to 16:00 in `notify.quiet_hours`. Notifications due inside quiet hours are held until 16:00, so every hand-off arrives during the posting window.

**Hand-off page** (`ace serve`, FastAPI on the Mac, reached over Tailscale). Auth: a long random token in a cookie set once via `/login?token=…`; Tailscale is the network boundary.
- `/` lists `ready` renders (newest first) and recent `approved` ones awaiting a post.
- `/handoff/{render_id}` shows:
  - a video preview, served from `/handoff/{id}/preview` (HTTP Range support for scrubbing). **Previewing is not approval.**
  - a **Save video** button.
  - one card per target platform, with its caption and a **Copy caption** button, plus a short posting reminder (turn on *Trial*).
  - a **Reject** button with reason chips (`off_brand`, `bad_shot`, `bad_timing`, `bad_text`, `boring`, `other`) + optional note.
  - after approval, a **Link post** field to paste the post URL if auto-matching misses it (§7.6).
- **Download = approval.** *Save video* calls `/handoff/{id}/download`. The server records `downloaded_at`, increments `download_count`, moves the render `ready → approved` (only on the first download) and creates one `posts` row per target in `awaiting_post`, all in one transaction. Then it returns the file. On phones that support the Web Share API with files (iOS Safari), the page fetches that endpoint and opens the share sheet, so *Save Video* puts it straight into Photos. Otherwise it's a plain `Content-Disposition: attachment` link. Repeat downloads are allowed and don't create duplicate posts.
- A rejected or superseded render can't be downloaded (the button is gone; the endpoint returns 409).
- Renders not downloaded within `handoff.expire_hours` (default 72) of their notification (`notified_at`) become `superseded`; a render not yet notified never expires. `ace notify pending` runs expiry before sending. Superseded counts as a weak negative in the taste prior (weight in config, default 0.25 of a reject).
- Templates are plain Jinja + minimal CSS + a few lines of vanilla JS for share/copy; no JS framework.

### 7.6 Post matching and metrics (Phase 1)

Since the owner posts by hand, the system has to find each post on the platform and link it back to its render. All platform calls are read-only, with no scraping or browser automation.

**Platform readers**
- **Instagram** (Meta Graph API, Instagram Business/Creator account). `GET /{ig-user-id}/media` with fields `id, permalink, timestamp, caption, media_type, media_product_type`, and per-media insights (`views`, `reach`, `saved`, `shares`, `ig_reels_avg_watch_time`, `reels_skip_rate`). Read scopes only (insights + basic). Long-lived token refresh is a scheduled check; the job fails loudly a week before expiry. *To verify in setup:* trial reels appear in the media list with insights before graduation (see §14).

As built:
- Instagram's media API returns no duration, so the duration filter is skipped for IG candidates.
- `ace auth meta` swaps a short-lived Graph API Explorer token (read scopes) for a ~60-day token; the owner re-runs it when warned.
- Manual links are resolved to the numeric IG media id and `posted_at` on the next `ace measure due`.

**Matching** (`ace measure due`, step 1): for each platform with `awaiting_post` rows:
1. Fetch the platform's videos posted since the oldest `downloaded_at` among those rows (minus 1 h slack).
2. Drop videos already linked to a post.
3. `match_posts(awaiting, candidates) -> list[Match]` is a pure function. Scoring per (post, video) pair:
   - **hard filters:** posted after `downloaded_at`, and duration within `match.duration_tolerance_s` (default 0.5 s) of `renders.duration_s`;
   - **score:** caption similarity (normalized token overlap against the provided caption, since the owner may edit it) plus time proximity to download.
   - Assign greedily by best score with a uniqueness constraint. A pair below `match.min_score` stays unmatched.
4. Matched → `matched` with `external_id`, `permalink`, `posted_at`, `match_method=auto`, `match_score`.
5. Still unmatched after `match.window_hours` (default 72 after download) → `not_posted`. The hand-off page's *Link post* field sets `matched` with `match_method=manual` at any time.

**Metrics** (step 2): matched posts where `posted_at + 24h/72h <= now` and that checkpoint row is missing → `fetch_metrics` → insert. Fields a platform doesn't return stay NULL.

### 7.7 Learn (Phase 2)
- **Reward per post** (IG trials): `success = 1` if the post beats the rolling **trial baseline** (median of the last `learn.baseline_window` trials, default 30) on the primary metric for that variable (table below), else 0. Binary rewards keep the Beta-Bernoulli model simple and robust to noisy magnitudes.
- **Per-variable scoring:** each matched post updates `(variable, value, platform)` Beta posteriors for every variable in its recipe. Approval signals update a separate `internal` platform row as a taste prior: approved (downloaded) = success; rejected = failure; superseded = weighted failure. A render that was downloaded but never posted (`not_posted`) is neutral.

  | Variable(s) | Primary metric |
  | --- | --- |
  | `opening_shot`, `snippet_type` | skip rate (lower wins) |
  | `cut_rate`, `length_s`, `footage_category`, `footage_orientation`, `look`, `effects`, `on_screen_text` | avg watch time ÷ length |
  | tie-breaker | (shares + saves) / reach |

- **Batch generation:** for each render slot, with probability 0.8 build a recipe by Thompson-sampling each variable independently from its posterior (exploit); with 0.2 pick uniformly (explore). The taste prior multiplies in as a soft weight (a reject-heavy value is down-weighted, not banned). Reference recipes enter as whole recipes in the explore share.
- A value is "settled" when `n_posts >= 8` and the posterior's 90% interval excludes the baseline.
- Guardrail: no recipe hash rendered more than `recipes.max_repeats_per_week` (default 3).
- Graduation of trial reels to followers is done by the owner in the Instagram app. The weekly report recommends which trials to graduate.

### 7.8 Weekly report and shoot list (Phase 2)
- Report rendered to HTML (served at `/report/<week>` by `ace serve`, with a notification when it's ready) plus a short summary.
- Contents: winners, losers and confidence per variable; trials worth graduating; hand-off funnel (ready → downloaded → posted); unmatched posts to link.
- Shoot list = winning footage traits (`category`, `setting`, `lighting`, high-motion vs static) × library gaps (count of high-quality shots matching each winning trait, vs what next week's planned recipes need). Generated text is templated from structured findings; an LLM may phrase it, but the numbers come from the DB.

### 7.9 Style references (Phase 3)
Reuses the Drive poller with the references folder. Measurement (scene cuts, beat tracking, color stats, OCR, motion) is local; the vision model fills in the semantic parts. Output is a `StyleCard` model, mapped to the nearest point in the recipe space → `recipes` row with `source=reference:<id>`, plus `recreation_notes` for traits the system can't produce. Reference media is never used in renders; only measurements are kept.

### 7.10 Micro edits (D12)

Sometimes the owner has reels that are already edited the way they want them. The job is to post them again and again as Trial Reels, each copy changed just enough that Instagram doesn't treat it as a duplicate. These are **micro renders**. They are separate from recipe renders in every table, command and page, and are never measured or learned from. Instagram's duplicate detection isn't public, so there's no guarantee a variant passes; the edit ranges are config so they can be tuned.

**Input.** A folder in the footage inbox whose name ends in `_micro` (any case, e.g. `you dont dance_micro`) is a **micro set**. It holds any number of finished videos and exactly one `.txt` file:

```
song: You Dont Dance
caption: you don't dance
cutoff: 0:02.4
POV: you finally hit the drop
wait for it
```

- The first non-empty line must be `song: <name>`. The song name is what the hand-off page and the notification show as the caption. The folder name is only a label.
- Then two optional lines, in either order, each at most once (key names in any case):
  - `caption: <text>`: the post caption, stored on the set (`micro_sets.caption`) for future auto-uploads. It isn't shown or used anywhere yet.
  - `cutoff: <time>`: when the on-screen text disappears, as a time on the *source* video. It takes `m:ss.s` (`0:02.4`) or plain seconds (`2.4` or `2.4s`) and must be after 0:00. Without it, the text stays for the whole render.
- Every other non-empty line is one on-screen text option (at least one), shared by all the set's videos. A `caption:` or `cutoff:` line after the first text option is just a text option.
- The videos have no text burned in. Each video's own audio track is used.

The folder stays in the inbox and is **live**: every run re-reads it, so videos added later join the rotation, deleted videos stop being used, and edits to the `.txt` take effect. Nothing is moved in Drive. Removing the folder from the inbox (or renaming it) stops the set. The footage poller (§7.1) only lists videos directly in the inbox, so it never sees files inside micro folders.

**Ingest** (`ace micro ingest`), per run:
1. List the inbox's subfolders (`DriveClient.list_folders`). Each `_micro` folder not yet in `micro_sets` is inserted as `waiting`. A set whose folder is no longer listed becomes `removed` (it comes back if the folder reappears).
2. For each set that isn't `removed`, list its files (`DriveClient.list_files`). The `.txt`: when its id or md5 differs from `sidecar_drive_file_id`/`sidecar_md5`, download and parse it. Valid → store `song_title` and `text_options_json` and the set is `active` (`next_text_index` is kept; it's taken modulo the option count). Missing, more than one `.txt`, or invalid → the problem goes in `error`; a set that never had a valid sidecar stays `waiting`, and an `active` one keeps its last good texts.
3. Videos (`mime_type` `video/*`): an unseen file id becomes a `micro_sources` row `new`. A video whose md5 matches another non-`removed` micro source is a `duplicate` and is never downloaded. A known video no longer in the folder becomes `removed` and its local copy is deleted; if it reappears it goes back to `new`. A video whose md5 changed goes back to `new`.
4. Each `new` video is downloaded to `media/micro/micro_source_<id><suffix>` and probed (duration, audio). Shorter than `micro.min_source_s` → `failed` (owner error, no retry). Otherwise `ready`. Download and probe failures count `attempts`; at `micro.max_attempts` → `failed`.

Commits happen after each state change, so no write transaction is open during a Drive call.

As built: duplicates are checked only when a video is inserted, reappears or changes, so a duplicate stays `duplicate` if the original is later removed. A removed set keeps its videos' rows and local files, so restoring the folder downloads nothing again. An invalid `.txt` is re-read every run until it's fixed.

**Edits** (pure: `sample_edit(rng, settings.micro, text) -> MicroEdit`). Every variant draws fresh values, each from a config range:
- trim: 0.1–0.5 s off the start and off the end;
- speed: 1–3% faster or slower, applied to video and audio together (the audio is resampled, so its pitch moves with it);
- zoom: 2–5%, cropped inside the source's own frame, with the crop window shifted randomly inside the zoom margin;
- mirror: horizontal flip with probability `micro.mirror_probability` (0.5);
- color: brightness ±0.03, contrast 0.95–1.08, saturation 0.9–1.15, hue ±4°;
- text: the source's next text option in file order, cycling forever. It's placed at the top or center, with its size within ±10% of the base and a small vertical jitter, and wrapped onto lines of at most `micro.text_max_chars` characters. With a set cutoff, the job copies it into the edit (`MicroEdit.text_cutoff_s`), and the text stops at `(cutoff − trim_start) / speed` into the render, so it disappears at the same moment of the content. A cutoff inside the trimmed start draws no text.

The variant is re-encoded with the render settings (1080×1920, 30 fps), with source metadata stripped. The source keeps its aspect ratio: it's fitted inside 1080×1920 and padded with black (a square reel gets bars above and below), after the color edits so the bars stay black. The text is white with a black stroke, in `micro.font_file` (the owner's machine uses Helvetica Bold, extracted from macOS's `Helvetica.ttc` into `~/.config/auto-content/fonts/`, since FFmpeg's drawtext can't pick a face inside a `.ttc` and Helvetica can't be committed). Unset, it falls back to `render.font_file`. Its duration is (source duration − trims) / speed. Validation checks that duration within one frame, the 9:16 size, and an audio stream if the source has one.

**Schedule** (`ace micro render`). `micro.post_times` (default 09:00, 13:00 and 18:00 in `notify.timezone`) are the daily slots. Each run fills every slot today that has no micro render yet and isn't more than `micro.late_grace_minutes` past. A slot takes the `active` set used least recently, then that set's `ready` video used least recently (never used first, ties by id), so sets take turns and each set's videos get used evenly. The text is the set's next option (`text_options_json[next_text_index % len]`), so consecutive posts from a set never reuse a text, whichever video they use. `last_used_at` (set and video) is the slot's `scheduled_for`, not the run time, so slots filled in the same run still rotate. A slot with any micro render, failed included, counts as filled; the render itself retries up to `render.max_attempts`, like recipe renders.

**Notify** (`ace micro notify`). First, `ready` micro renders notified more than `micro.expire_hours` (24) ago become `superseded`. Then one notification goes out per `ready` micro render whose `scheduled_for` has passed and whose `notified_at` is NULL: title `Micro ready: <song>`, linking to `/micro/{id}`. Quiet hours don't apply, since the slots are the timing.

**Hand-off.** The index page has its own *Micro edits* list. `/micro/{id}` shows the preview, *Save video*, and the caption (the song name) with *Copy caption*. The first download moves `ready → downloaded`. There's no reject button, no `posts` row and no matching. A superseded micro render can't be downloaded (409).

`ace micro run` runs ingest, render and notify in that order, so one 15-minute scheduled job covers everything. `ace micro list` shows each set (status, song, texts, next text) and its videos with their render counts.

---

## 8. Configuration and secrets

- `engine/config.py` defines a single `Settings` object (pydantic-settings). Precedence: env vars > `config/local.toml` (gitignored) > `config/default.toml`.
- Paths: `data_dir` (default `./data`), with `db`, `media/scratch`, `media/shots`, `media/renders`, `secrets` beneath it. Nothing outside `data_dir` is written.
- Secrets, env only (`.env` locally, secret manager in the cloud):
  - `GOOGLE_OAUTH_CLIENT_JSON`, `ANTHROPIC_API_KEY`;
  - `META_APP_ID`, `META_APP_SECRET`, `META_IG_USER_ID`;
  - `NTFY_SERVER` (default `https://ntfy.sh`), `NTFY_TOPIC`, `NTFY_TOKEN` (optional);
  - `HANDOFF_BASE_URL` (Tailscale URL), `HANDOFF_TOKEN`.

  OAuth refresh tokens are written to `data/secrets/` with `0600` permissions.
- Vision model id is config (`vision.model`, default `claude-sonnet-5-5`), never hardcoded.

### Cloud-readiness checklist
Kept true from day one so a move is a deploy, not a refactor:
- [ ] No absolute or Mac-specific paths; everything under `data_dir`.
- [ ] `Dockerfile` builds and `ace --help` runs in it (CI check).
- [ ] `MediaStore` is the only code touching media file locations (in the cloud, the hand-off download becomes a signed bucket URL, and the approval is recorded before the redirect).
- [ ] DB URL is config; no SQLite-only SQL in queries.
- [ ] Jobs are single-shot commands, so any scheduler can run them.

---

## 9. Coding conventions

- **Tooling:** `uv` for env and deps; `ruff` (lint + format); `mypy --strict` on `engine/`; `pytest`. `make check` (or `uv run task check`) runs all four and must pass before a commit.
- **Style:** readable names over abbreviations; small functions; match surrounding code. Comments only for non-obvious *why*. Type hints everywhere in `engine/`.
- **Models:** Pydantic for domain models and anything crossing a boundary (API payloads, config, JSON columns). SQLAlchemy models stay in `engine/db`; convert to domain models at the repository layer.
- **Time:** all timestamps UTC, timezone-aware. Media times are `float` seconds; frame-accurate math rounds to the output fps in one helper.
- **Randomness:** any function that samples takes an explicit `random.Random` (or numpy `Generator`). No global RNG.
- **Subprocesses:** FFmpeg/ffprobe are called through one wrapper that logs argv, captures stderr, and raises a typed error with the stderr tail.
- **External HTTP:** one client per service, with timeouts, retries with exponential backoff on 429/5xx, and no retry on 4xx. Rate limits live in config.
- **Logging:** stdlib `logging` with JSON output; every log line in a job includes the job name and the row id it is working on. Never log tokens, the ntfy topic or the hand-off token.
- **Errors:** expected failures (scene split finds zero shots, a notification fails) are recorded on the row; unexpected exceptions are logged with traceback, recorded, and the job continues with the next item.
- **Git:** small commits, one concern each; migrations committed with the model change that needs them.

---

## 10. CLI surface

```
ace init                        # create data dirs, run migrations, check ffmpeg
ace auth google | meta          # one-time auth flows (meta is read-only scopes)
ace ingest footage | references
ace snippets add <audio> --start S --end S --type hook|drop|intro_build
ace snippets list
ace render batch [--n N] [--recipe ID] [--snippet ID] [--dry-run]   # dry-run prints timelines, no notifications
ace render one --recipe ID --snippet ID                            # dev loop
ace notify test                 # send a test notification to the phone
ace notify pending              # send hand-off notifications for ready renders (respects the posting window)
ace serve [--host --port]       # hand-off page (bind 127.0.0.1, expose with `tailscale serve --bg 8000` for HTTPS)
ace measure due                 # match posts, then pull due metrics
ace posts link <render_id> <platform> <url>                        # manual match
ace learn update
ace report weekly
ace status                      # counts per table/status, recent failures
ace micro ingest | render | notify | run                           # micro edits (§7.10); run = all three
ace micro list                  # micro sets, their videos and render counts
```

---

## 11. Testing strategy

| Layer | What | How |
| --- | --- | --- |
| Unit | beat grid → cut points, drop snapping, shot selection, `assemble`, render/post state transitions, `match_posts`, bandit sampling and updates, reward computation, recipe hashing | Pure functions; seeded RNG; property tests (Hypothesis) for timeline invariants: total duration = target, every cut on the grid (any beat for `one_long_shot`), no shot reused within a video, a cut at the drop when required |
| Integration | scene split, FFmpeg render, ffprobe validation, DB repositories, migrations, hand-off endpoints (download → approval exactly once, preview ≠ approval, 409 on rejected) | Real ffmpeg + temp SQLite against tiny fixtures; FastAPI `TestClient` |
| Contract | Drive, vision, ntfy, Meta clients | Recorded responses (respx/vcrpy); fakes implement the same Protocols |
| End-to-end (local) | `ingest → render → notify → download → measure (match + metrics)` | One test against fakes, run in CI |

A guard test asserts that no module under `engine/` imports or calls a known publish endpoint (Meta `media_publish` / `/media` POST), enforcing principle 6.

Fixtures are **generated, not committed binaries**: a `tests/fixtures/make_fixtures.py` builds a few short clips with ffmpeg `lavfi` (color bars, moving test patterns, hard scene changes) and a click track at a known BPM, so beat and scene detection have ground truth.

Tests are written first and must fail before the code is written. The workflow is in [CLAUDE.md](../CLAUDE.md#test-first-workflow).

No test touches the network. CI: GitHub Actions on push (`make check` + Docker build).

---

## 12. Milestones (Phase 1 broken into shippable slices)

Each slice ends with something runnable and tested.

1. **Skeleton**: pyproject, config, DB + first migration, CLI with `init`/`status`, Dockerfile, CI.
2. **Drive ingest**: OAuth, poller, dedup, download, move to Processed.
3. **Shot library**: scene split, proxies, motion/quality heuristics, vision tagging.
4. **Snippets**: `snippets add`, beat grid, drop detection.
5. **Assembly**: Recipe model, 3 fixed recipes, selection + `assemble` → Timeline (`render batch --dry-run`).
6. **Render**: FFmpeg pipeline, LUTs, text, validation.
7. **Notify**: `Notifier` + ntfy, quiet hours, retry of unsent notifications, `ace notify test`.
8. **Hand-off page**: `ace serve`, preview, save/share, download = approval, reject, expiry, Tailscale runbook.
9. **Platform readers**: Meta read-only auth, `recent_videos`, `match_posts`, manual link.
10. **Measure**: 24 h / 72 h metric pulls.

**Micro edits** (§7.10, D12):

16. **Micro foundation** (main agent): `micro_sets`/`micro_sources`/`micro_renders` + migration 0003, `[micro]` config, `MediaKind.MICRO`, `DriveClient.list_files`/`list_folders`, `engine/micro/models.py` contracts and stubs.
17. **Micro edits**: `sample_edit`, the FFmpeg command, render and validation.
18. **Micro ingest**: sidecar parsing, `poll_micro_sets`.
19. **Micro schedule**: slots, set and video rotation, the render job, notifications and expiry, `ace micro …`.
20. **Micro hand-off**: index list, `/micro/{id}` page, download.

Phases 2–4 follow the README roadmap; each gets broken into slices here before work starts. Build order follows the prototype-first rule: get each slice working end to end before adding refinements, and add a refinement only when real usage shows the simpler version falls short.

---

## 13. Decision log

| Date | ID | Decision | Why |
| --- | --- | --- | --- |
| 2026-11-08 | D1 | Local first, cloud-ready | Cheapest and easiest to debug; config/storage abstraction + Dockerfile keep the move cheap. |
| 2026-11-08 | D2 | Minimal FastAPI page over Tailscale | Full control of the review UX; Tailscale avoids exposing the Mac publicly. |
| 2026-11-08 | D3 | Drive OAuth on owner account | Owner uploads the files, so owner auth can move them; one-time setup. |
| 2026-11-08 | D4 | ~~TikTok inbox drafts via own app~~ | Superseded by D4b, then D8. |
| 2026-11-08 | D4b | ~~TikTok via Upload-Post~~ | Superseded by D8. Research notes, in case API posting is ever reconsidered: Upload-Post Basic ($24/mo) supports TikTok direct + draft posts and IG trial reels (`share_mode`); alternatives are Zernio, Blotato and Ayrshare. |
| 2026-11-08 | D8 | Manual hand-off for all platforms; system never posts | Owner requirement: nothing goes live without the owner posting it from the app. Instagram's API has no draft mode (any API upload goes live), so a hand-off is the only way to meet this for IG. Doing the same for TikTok keeps one workflow and removes posting-API dependencies, audits and caps. |
| 2026-11-08 | D9 | Download from the hand-off page = approval | One touch per video; the download is a clear, server-observable intent signal. |
| 2026-11-08 | D10 | ntfy for phone notifications | Free, iOS + Android, one HTTP POST with a click URL. Behind `Notifier`, so swappable. |
| 2026-11-08 | D6 | SQLite + SQLAlchemy + Alembic | Zero-ops now, Postgres-compatible later. |
| 2026-11-08 | D7 | DB-driven jobs, no broker | Volume is tiny (a few renders/day); a queue adds ops with no benefit. |
| 2026-11-08 | — | Binary reward vs rolling trial baseline for the bandit | Metrics are noisy and differently scaled; a Beta-Bernoulli per variable is simple and robust. Revisit with more data. |
| 2026-11-08 | — | Render from local proxies, not Drive originals | Renders become fast and independent of Drive availability. |
| 2026-11-08 | — | Match manual posts by duration + caption similarity + time, with manual link fallback | No ID links a hand-posted video to its render; these signals are available from both read APIs. Frame-fingerprint matching was considered and deferred until the match rate shows it's needed. |
| 2026-11-09 | — | Hand-off expiry counts from `notified_at`; unnotified renders never expire | Notifications are held for quiet hours, so counting from `created_at` shortened the owner's real window by up to ~10 h. |
| 2026-11-09 | — | Snippet start snaps to the nearest downbeat | First real run: every video started mid-beat, with the first cut 0.3–0.4 s in. |
| 2026-11-09 | — | `footage_orientation` (portrait · landscape) is a recipe variable; a render never mixes orientations | First real run: landscape music-video shots center-cropped to 9:16 lose content and look different from portrait phone footage. Making it a variable keeps renders consistent and lets the learning loop measure whether cropped landscape footage performs. |
| 2026-11-08 | — | ~~TikTok watch time from a weekly TikTok Studio export~~ | Superseded by D11. |
| 2026-11-14 | D11 | Instagram Trial Reels only; TikTok removed | Owner decision to cut scope. Removed: the TikTok reader and `ace auth tiktok`, TikTok link parsing, the TikTok cadence and caption config, the TikTok Studio export import (`ace ingest exports`, `metric_imports`, the `export` metric checkpoint/source). Migration 0002 drops `metric_imports` and deletes TikTok rows. The multi-platform plumbing stays so a platform can be added back. |
| 2026-11-14 | D12 | Micro edits from `<song>_micro` folders, separate from recipe renders | Owner request: post finished reels 3× a day as Trial Reels without duplicate flags. The owner chose: no burned-in text in the uploads; a folder of several videos plus one `.txt` with `song:` and the text options; the folder stays live in the inbox; all edit types (mirror and audio speed shift included); unlimited reuse, sets and videos least recently used first, texts cycling per set; three slots spread across the day; no tracking or learning. A first draft used single `*_micro` files with a same-name sidecar; migration 0003 was reworked before any database applied it. |
| 2026-11-18 | — | Micro `.txt` gains optional `caption:` and `cutoff:` lines; the cutoff is a time on the source video | Owner request: a caption stored for future auto-uploads, and on-screen text that disappears at a set moment. Measuring the cutoff on the source keeps it on the same moment of the content despite each render's trim and speed change. The hand-off page still copies the song name (migration 0004). |
| 2026-11-18 | — | Micro renders keep the source's aspect ratio (black bars) and use Helvetica Bold | Owner request after the first micro run: cropping square reels to 9:16 cut off content. |

---

## 14. Open questions

| Question | Default until decided |
| --- | --- |
| Notification service: ntfy, Pushover or Telegram? | ntfy (D10); swappable behind `Notifier`. |
| Do trial reels appear in `GET /{ig-user-id}/media` with insights before graduation? | Verify in slice 9. If not, fall back to manual links + insights by media id, and recheck after graduation. |
| Is the match rate good enough without frame fingerprints? | Track the auto-match rate in `ace status` and the weekly report; revisit if manual links are frequent. |
| Captions and hashtags: templated or generated per post? | Templated per song from config. |
| Rejection reasons: hard filter or prior? | Soft prior (§7.7). |
| Where does song audio come from? | Local file passed to `snippets add`. |
| Face-aware 9:16 reframing? | Center crop in Phase 1; revisit if rejections cite framing. |
| Closest openly licensed match for Instagram's in-app text font? | Compare candidates (e.g. Instrument Sans) side by side against an in-app screenshot during the native-text task; the owner can supply their own font file. |

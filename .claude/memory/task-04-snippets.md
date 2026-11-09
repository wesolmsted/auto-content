---
name: task-04-snippets
description: Slice 4 snippets: song analysis (beat-grid correction for librosa), drop detection, BeatGrid for a window, songs/snippets store, `ace snippets add|list`; accuracy and gotchas
type: project
---

Slice 4 built song analysis, drop detection and the snippet CLI in `engine/snippets/`.

**Why:** recipes/assembly (slice 5) and render need a `BeatGrid` per snippet, and the snippets table is what `render batch` picks from. **How to apply:** call the functions below; don't re-run analysis yourself, read it from `songs` via the store.

## Public API
- `analysis.analyze_song(audio_path, settings: SnippetSettings) -> SongAnalysis`. Whole song, absolute seconds. Raises `ValueError` if librosa finds fewer than 2 beats.
- `analysis.detect_drop(audio_path, song, start_s, end_s, settings) -> float | None`. Returns an absolute song time that is always one of `song.downbeats`, or None.
- `analysis.beat_grid_for_snippet(song, start_s, end_s, drop_s) -> BeatGrid`. Pure. `drop_s` is absolute and must be within `[start_s, end_s]`; `end_s > start_s`. Both are checked with `ValueError`. Beats/downbeats are the song's own values inside the window (inclusive at both ends), minus `start_s`.
- `store.find_song(session, audio_path) -> (song_id, SongAnalysis) | None`, `store.add_song(session, audio_path, analysis) -> id` (title = file stem), `store.add_snippet(session, song_id, type, start_s, end_s, drop_s, label) -> id`, `store.list_snippets(session) -> list[SnippetSummary]` (with `song_title`, ordered by id). They flush but don't commit.
- `snippets.drop_s` is stored as an **absolute song time**, like `start_s`/`end_s`. Pass it straight to `beat_grid_for_snippet`.

## Beat/tempo correction (the slice-1 117.5 BPM issue)
librosa `beat_track` alone gave 117.45 BPM on the 120 BPM fixture: frame-quantized beats (±29 ms at hop 512), with the first 2 s skipped. Now:
1. Hop length 256 (`HOP_LENGTH`), so frames are 11.6 ms.
2. `beat_track` beats only seed the grid. Each gap is rounded to whole beats against the median gap, so skipped beats count as 2. Then `np.polyfit(beat_number, time)` gives the period and phase. The median gap alone is quantized (0.511 s) and drifts if used to index absolute beats.
3. Phase is corrected by the median signed offset of `onset_detect(backtrack=True)` onsets within a quarter beat of the grid. The onset-envelope peak lags the real onset by ~20 ms.
4. The constant-tempo grid covers the sound (`librosa.effects.trim`, top_db 60): beats from first sound minus a quarter beat, to last sound minus a quarter beat, clipped to [0, duration].
5. Downbeats: the phase k in 0..beats_per_bar-1 whose beats have the highest mean peak onset strength in the quarter beat after each beat.

Measured on the fixture: 120.008 BPM, 40/40 beats, max error 7.2 ms, downbeats 0, 2, …, 18 s (all within 7 ms).

## Drop rule
Use RMS energy per frame (hop 256). For each frame, jump = 10·log10(mean energy over the next bar / mean over the previous bar); the bar length is from `song.bpm` and `beats_per_bar`. Both bars must fit in the song but may extend outside the window. Take the max over frames inside the window. If it is under `MIN_DROP_JUMP_DB = 3.0`, or there's no downbeat in the window, return None; otherwise snap to the nearest downbeat in the window. On the fixture the peak is 19.0 dB at 7.96 s, which snaps to 7.994. A steady section peaks at 0.02 dB. RMS only, not onset strength: per-click onset transients would make every beat a candidate.

## Song caching
Songs are keyed on `str(audio_path.resolve())` in `songs.audio_path`, with `analyzed_at` set. There's no content hash column, so a file replaced at the same path keeps its stale analysis. Delete the row, or use a new path.

## CLI
- `ace snippets add <audio> --start S --end S --type hook|drop|intro_build [--drop S] [--label TEXT]`. Needs `ace init` first. Exits 1 with `error: …` if: the file is missing; end ≤ start; the window is outside the song; or `--drop` is outside the window. The drop is auto-detected for every type unless `--drop` is given. `--drop` is stored as is, without snapping.
- `ace snippets list`: aligned table `id song type start end drop label` (`-` for none).

## Gotchas
- Only the slice-1 click fixture has ground truth. Real songs aren't verified yet.
- The grid assumes a **constant tempo** (fine for produced pop/EDM; it breaks on live/rubato music or tempo changes). Bar phase from onset strength can pick beat 3 when the kick hits 1 and 3 equally. If either shows up, switch to madmom's `DBNDownBeatTrackingProcessor` (DESIGN §7.2 already allows this), or let the owner pass a downbeat offset.
- The thresholds (`HOP_LENGTH`, `SILENCE_TOP_DB`, `MIN_DROP_JUMP_DB`) are module constants, not in `config/default.toml`. Move them there if they need tuning.
- mypy: librosa is untyped, so `librosa.load`'s rate is typed `int | float`. Annotate helpers with `rate: float`.
- Tests: `tests/unit/test_snippets_beat_grid.py` (Hypothesis), `tests/integration/test_snippets_{analysis,store,cli}.py`.

---
name: task-17-micro-edits
description: Slice 17 micro edits: sample_edit, output_duration_s, build_micro_args/render_micro/validate_micro, the filter graph, text layout, pitch-shifted audio, test ffmpeg selection
type: project
---

Slice 17 filled in `engine/micro/edits.py` and `engine/micro/render.py` (DESIGN.md §7.10).

**Why:** slice 19 (schedule/job) calls these to render each slot. **How to apply:** call the functions below; the job owns retries and DB state.

## Public API
- `edits.sample_edit(rng, settings.micro, text) -> MicroEdit`: pure; every value uniform in its config range. Speed is `1 ± uniform(speed_change)` with a random sign; brightness, hue and `text_y_offset` are ± their scalar; `text_size_fraction` is the base × (1 ± jitter). The draw order is fixed, so the same seed gives the same edit.
- `edits.output_duration_s(edit, source_duration_s)` = (source − trims) / speed.
- `render.build_micro_args(edit, source, out_path, settings: Settings) -> list[str]`: pure argv minus the binary. Raises ValueError if the trims leave nothing.
- `render.render_micro(edit, source, out_path, settings) -> argv`: runs `settings.tools.ffmpeg`, then `validate_micro`. Raises FfmpegError or MicroRenderValidationError.
- `render.validate_micro(path, expected_duration_s, expect_audio, settings)`: duration within 1/fps, exactly width×height, audio stream iff `expect_audio`. The message lists every problem, like the recipe render's validate.

## Filter graph (one input)
- Video: `trim` → `setpts=(PTS-STARTPTS)/speed` → `scale` to cover (W×zoom, H×zoom), rounded to even → `crop=w=W:h=H:x=(iw-ow)*crop_x:y=(ih-oh)*crop_y` → `setsar=1` → `hflip` (mirror) → `eq` → `hue=h=` → one `drawtext` per line → `fps` → `format=yuv420p`.
- Text is drawn after the flip, so it stays readable. Lines come from `textwrap.wrap(text, micro.text_max_chars)`. Font size is `round(text_size_fraction × H)` and lines are `round(1.2 × size)` apart. The block's top is at 15% of H (top) or centered on its height `(n−1)·step + size` (center), plus `text_y_offset × H`. All y values are computed in Python as integers. The style (font, white, 5 px black border) comes from `TextRenderStyle.default(settings.render)`.
- Audio (only if `source.has_audio`): `atrim` → `asetpts` → `aresample=48000,asetrate=round(48000·speed),aresample=48000`. The output is always 48 kHz.
- Output: the render codecs/crf/preset/profile, `-r fps`, `-map_metadata -1`, `+faststart`, and `-t` the planned duration.

## Gotchas
- Every micro render has text, so **it always needs drawtext**. The Homebrew `ffmpeg` doesn't have it. Locally, point `tools.ffmpeg` at `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` (and `tools.ffprobe` alongside it).
- `tests/integration/test_micro_render.py` uses `ffmpeg` if it has drawtext, otherwise ffmpeg-full at that Homebrew path, and otherwise skips the render tests. The `validate_micro` tests always run.
- `MicroSettings` doesn't bound `speed_change` below 1 or `zoom` at ≥ 1. Out-of-range config makes `MicroEdit` validation fail inside `sample_edit`.
- Tests: `uv run pytest tests/unit/test_micro_edits.py tests/integration/test_micro_render.py` (about 8 s).

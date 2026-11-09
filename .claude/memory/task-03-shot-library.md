---
name: task-03-shot-library
description: Slice 3 shot library + vision tagging: build_shots behaviour and outputs, motion/quality definitions, proxy settings, vision prompt/response contract, fake tagger, measured accuracy, deviations, gotchas
type: project
---

Slice 3 fills in `engine/ingest/shots.py` and `engine/vision/claude.py`, plus helper modules `engine/ingest/{scenes,frames,quality,proxies}.py` and `engine/vision/prompts.py`.

**Why:** later slices (2 ingest job, 5 selection, 6 render, handoff) depend on the shape of `Shot`s, proxies and tags. **How to apply:** use the contracts below; ask the main agent before changing them.

## `build_shots(video_path, footage_file_id, tagger, store, settings: IngestSettings) -> list[Shot]`
- Steps: `probe` → `detect_scenes` (ContentDetector at `scene_threshold`, `start_in_scene=True`, so a clip with no cuts is one scene) → `plan_shot_spans` → one OpenCV pass over the whole file (`analyze_frames`) → per shot: tag, score, proxy, thumbnail.
- Spans are frame indices, `[start, end)`. `start_s = start_frame / fps` and `end_s = end_frame / fps`, using the ffprobe fps.
- Shorter than `min_shot_s` (rounded up to frames) is dropped. Longer than `max_shot_s` is split into `ceil(n / max)` equal parts, contiguous and within one frame of each other.
- `width`/`height`/`orientation` are the source's **display** size: coded size swapped when rotation is ±90. Orientation is exact: w>h landscape, w<h portrait, otherwise square.
- Tagger frames: `frames_per_shot` JPEGs (3 = first/middle/last frame of the shot), long side ≤768, uncropped, written to `MediaKind.SCRATCH` and deleted after the call, even if it fails.
- If the tagger raises `VisionTagError`, it propagates and nothing is returned. Proxies already written for earlier shots stay on disk, but names are deterministic (`footage<id>_<start:06d>-<end:06d>.mp4` / `.jpg` in `MediaKind.SHOTS`), so a retry overwrites them.
- Returns `[]` if no shot survives `min_shot_s`. The caller records that as "zero shots". Raises `ValueError` if there is no video stream.

## Scores (`engine/ingest/quality.py`, constants at the top)
- Frames are analysed in grayscale, downscaled to a 320 px long side. Per frame:
  - Farneback flow from the previous frame: mean magnitude, plus the median vector as "camera motion";
  - Laplacian variance.
  - The flow into a shot's first frame crosses the cut, so it's ignored.
- `motion_score` = mean flow, in long sides per second, ÷ 0.3, capped at 1. 1 ≈ a full-frame pan in ~3 s; testsrc2 ≈ 0.17–0.19; static = 0.
- `shake` = mean frame-to-frame change of the camera-motion vector ÷ 4 px, capped at 1. `sharpness` = mean Laplacian variance ÷ 100, capped at 1. Flat-colour frames read as 0, i.e. "blurry".
- `quality_score` = 0.6 × tagger quality + 0.4 × sharpness × (1 − shake).

## Proxies (`engine/ingest/proxies.py`)
- The largest centered 9:16 crop (even sides), then scaled **down only** so the width is ≤ `proxy_max_dim` (1080). A 1080x1920 source gives a 1080x1920 proxy, and 1280x720 gives 404x720.
- Encoding: libx264, crf 18, preset veryfast, yuv420p, GOP = round(fps) frames, no audio, `+faststart`, source fps kept. Exactly `frame_count` frames, from input seek `-ss floor_ms(start/fps)`. ffmpeg autorotates, so proxies have no rotation metadata.
- The thumbnail is the same 9:16 crop at 360 px wide, from the shot's middle frame.

## Vision contract (`ClaudeVisionTagger`, `engine/vision/prompts.py`)
- One `client.messages.create`:
  - `model` and `max_tokens` from `VisionSettings`;
  - a single user turn with the frames as base64 `image/jpeg` blocks, then the prompt text, which lists each vocabulary field's allowed values;
  - `output_config.format` = a JSON schema with `enum`s from the vocabulary and `QualityIssue`, every field required, `additionalProperties: false`.
- No thinking, temperature or fallbacks parameters are sent.
- The reply is validated again: `ShotTags.model_validate_json` plus a vocabulary membership check. All of these raise `VisionTagError`:
  - `stop_reason != "end_turn"` (e.g. `max_tokens`, `refusal`);
  - invalid JSON, a missing or extra field, quality outside 0..1;
  - an out-of-vocabulary value or issue;
  - any `anthropic.APIError`;
  - an unreadable frame.
- Building the client is the caller's job (slice 2), e.g. `anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value(), timeout=vision.timeout_s, max_retries=vision.max_retries)`.

## Fake (`tests/fakes/vision.py`)
`FakeVisionTagger(tags=DEFAULT_TAGS, error=None)`. It returns `tags` for every call, or raises `error` if set, and appends each call's frame list to `.calls`. `DEFAULT_TAGS` holds default-vocabulary values (city_night/person/street/night/energetic, has_face, quality 0.8).

## Measured on the fixtures
- Portrait clip: shots at exactly 0–2.5–5.0–7.5–10.0 s (0-frame error). Motion: 0.193 / 0.000 / 0.000 / 0.006. Quality with the fake's 0.8: 0.88 / 0.88 / 0.48 (flat blue) / 0.54.
- Landscape clip: 1 shot, 0–6 s, motion 0.174.
- `tests/integration/test_shots_build.py` runs in ~13 s. The portrait build is shared through a module-scoped fixture.

## Deviations / gotchas
- DESIGN §7.1 says "1080 px wide-side cap". Proxies are instead already center-cropped to 9:16, with the cap on the proxy **width**. A long-side cap of 1080 would make every render upscale ~1.8x. Renders only need to scale to the output size, and should not crop again.
- `build_shots` takes only `IngestSettings`, so ffmpeg/ffprobe come from PATH, not `tools.*`.
- Motion/quality constants are hardcoded heuristics, not config. Tune them on real footage.
- anthropic 2.x is built on `httpx2`: tests that build SDK errors need `httpx2.Request`, not `httpx`.
- OpenCV stubs reject `None` as the flow output arg, so pass a preallocated array to keep mypy happy.
- Run: `uv run pytest tests/unit/test_shots_* tests/unit/test_vision_* tests/integration/test_shots_build.py`.

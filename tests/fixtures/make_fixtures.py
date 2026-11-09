"""Generate the test media with known ground truth (DESIGN.md §11). Nothing binary is committed.

Run directly to inspect the files: `uv run python -m tests.fixtures.make_fixtures OUT_DIR`.
"""

import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import soundfile

from engine.ffmpeg import run_ffmpeg

PORTRAIT_SIZE = (1080, 1920)
PORTRAIT_DURATION_S = 10.0
PORTRAIT_SCENE_CHANGES_S = (2.5, 5.0, 7.5)

LANDSCAPE_SIZE = (1280, 720)
LANDSCAPE_DURATION_S = 6.0

VIDEO_FPS = 30

CLICK_SAMPLE_RATE = 44100
CLICK_BPM = 120.0
CLICK_BEAT_INTERVAL_S = 60.0 / CLICK_BPM
CLICK_DURATION_S = 20.0
CLICK_BEATS_PER_BAR = 4
CLICK_DROP_S = 8.0

_FAST_H264 = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]


def make_portrait_scenes(path: Path) -> Path:
    """1080x1920, 10 s, 30 fps, with hard cuts at PORTRAIT_SCENE_CHANGES_S. No audio."""
    width, height = PORTRAIT_SIZE
    boundaries = (0.0, *PORTRAIT_SCENE_CHANGES_S, PORTRAIT_DURATION_S)
    sources = ["testsrc2", "smptehdbars", "color=c=0x1e3a8a", "rgbtestsrc"]
    inputs: list[str] = []
    for source, (start, end) in zip(sources, pairwise(boundaries), strict=True):
        separator = ":" if "=" in source else "="
        spec = f"{source}{separator}size={width}x{height}:rate={VIDEO_FPS}:duration={end - start}"
        inputs += ["-f", "lavfi", "-i", spec]
    streams = "".join(f"[{index}:v]" for index in range(len(sources)))
    concat = f"{streams}concat=n={len(sources)}:v=1:a=0,format=yuv420p[video]"
    run_ffmpeg([*inputs, "-filter_complex", concat, "-map", "[video]", *_FAST_H264, str(path)])
    return path


def make_landscape_motion(path: Path) -> Path:
    """1280x720, 6 s, 30 fps moving test pattern with a 440 Hz tone."""
    width, height = LANDSCAPE_SIZE
    duration = LANDSCAPE_DURATION_S
    run_ffmpeg(
        [
            *[
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={width}x{height}:rate={VIDEO_FPS}:duration={duration}",
            ],
            *["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"],
            *_FAST_H264,
            *["-c:a", "aac", "-shortest", str(path)],
        ]
    )
    return path


def make_click_track(path: Path) -> Path:
    """20 s mono WAV at exactly 120 BPM: a click on every beat starting at 0, an accent on every
    4th beat (the downbeats), and a ~3x loudness jump plus a bass bed from CLICK_DROP_S."""
    rate = CLICK_SAMPLE_RATE
    samples = np.zeros(int(CLICK_DURATION_S * rate))
    click_time = np.arange(int(0.03 * rate)) / rate
    beat_count = round(CLICK_DURATION_S / CLICK_BEAT_INTERVAL_S)
    for beat in range(beat_count):
        start_s = beat * CLICK_BEAT_INTERVAL_S
        is_downbeat = beat % CLICK_BEATS_PER_BAR == 0
        amplitude = 0.3 if is_downbeat else 0.15
        if start_s >= CLICK_DROP_S:
            amplitude *= 2.5
        frequency = 1500.0 if is_downbeat else 1000.0
        click = amplitude * np.sin(2 * np.pi * frequency * click_time) * np.exp(-click_time / 0.008)
        start = round(start_s * rate)
        samples[start : start + len(click)] += click
    drop = round(CLICK_DROP_S * rate)
    bed_time = np.arange(len(samples) - drop) / rate
    samples[drop:] += 0.15 * np.sin(2 * np.pi * 60.0 * bed_time)
    soundfile.write(path, samples, rate, subtype="PCM_16")
    return path


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    make_portrait_scenes(out_dir / "portrait_scenes.mp4")
    make_landscape_motion(out_dir / "landscape_motion.mp4")
    make_click_track(out_dir / "click_120bpm.wav")


if __name__ == "__main__":
    main(Path(sys.argv[1]))

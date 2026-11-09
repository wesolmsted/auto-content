"""Scene splitting and shot planning (DESIGN.md §7.1 step 4). Spans are frame indices."""

import math
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple

from scenedetect import ContentDetector, detect


class ShotSpan(NamedTuple):
    """Frames `start_frame` (inclusive) to `end_frame` (exclusive) of a video."""

    start_frame: int
    end_frame: int

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame


def detect_scenes(video_path: Path, threshold: float) -> list[ShotSpan]:
    """Split at hard cuts with PySceneDetect's ContentDetector. No cuts gives one scene."""
    scenes = detect(str(video_path), ContentDetector(threshold=threshold), start_in_scene=True)
    return [ShotSpan(int(start.frame_num), int(end.frame_num)) for start, end in scenes]


def plan_shot_spans(
    scenes: list[ShotSpan], fps: float, min_shot_s: float, max_shot_s: float
) -> list[ShotSpan]:
    """Drop scenes shorter than `min_shot_s`; split longer than `max_shot_s` into equal parts."""
    min_frames = math.ceil(min_shot_s * fps - 1e-9)
    max_frames = math.floor(max_shot_s * fps + 1e-9)
    spans: list[ShotSpan] = []
    for scene in scenes:
        if scene.frame_count < min_frames:
            continue
        parts = math.ceil(scene.frame_count / max_frames)
        boundaries = [
            scene.start_frame + scene.frame_count * part // parts for part in range(parts)
        ]
        boundaries.append(scene.end_frame)
        spans += [ShotSpan(start, end) for start, end in pairwise(boundaries)]
    return spans

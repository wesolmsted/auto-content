"""Local motion and quality heuristics (DESIGN.md §7.1 step 5), measured with OpenCV."""

from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
from cv2.typing import MatLike

from engine.ingest.scenes import ShotSpan

ANALYSIS_LONG_SIDE = 320
MOTION_FULL_SCALE = 0.3
SHAKE_FULL_SCALE_PX = 4.0
SHARPNESS_FULL_SCALE = 100.0
TAGGER_WEIGHT = 0.6


class FrameStats(NamedTuple):
    """One frame, downscaled to ANALYSIS_LONG_SIDE.

    `flow_px` is the mean optical-flow magnitude from the previous frame (0 for the first),
    `global_motion` the median flow vector (camera motion), `sharpness` the Laplacian variance.
    """

    flow_px: float
    global_motion: tuple[float, float]
    sharpness: float


class ShotHeuristics(NamedTuple):
    """All in 0..1. `shake` is jitter in camera motion; `sharpness` is 0 for blurry/flat frames."""

    motion_score: float
    sharpness: float
    shake: float


def analyze_frames(video_path: Path) -> list[FrameStats]:
    """Per-frame stats for the whole video in one decoding pass."""
    capture = cv2.VideoCapture(str(video_path))
    stats: list[FrameStats] = []
    previous: MatLike | None = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            gray = _analysis_gray(frame)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            if previous is None:
                stats.append(FrameStats(0.0, (0.0, 0.0), sharpness))
            else:
                flow = np.zeros((*gray.shape, 2), np.float32)
                flow = cv2.calcOpticalFlowFarneback(previous, gray, flow, 0.5, 3, 15, 3, 5, 1.2, 0)
                magnitude = float(np.linalg.norm(flow, axis=2).mean())
                dx, dy = np.median(flow.reshape(-1, 2), axis=0)
                stats.append(FrameStats(magnitude, (float(dx), float(dy)), sharpness))
            previous = gray
    finally:
        capture.release()
    return stats


def _analysis_gray(frame: MatLike) -> MatLike:
    height, width = frame.shape[:2]
    scale = ANALYSIS_LONG_SIDE / max(height, width)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def shot_heuristics(stats: list[FrameStats], span: ShotSpan, fps: float) -> ShotHeuristics:
    """Aggregate the frames of `span`. The flow into its first frame crosses a cut, so it's skipped.

    motion_score: mean flow speed in long-sides per second, over MOTION_FULL_SCALE, capped at 1.
    shake: mean frame-to-frame change of the camera-motion vector, over SHAKE_FULL_SCALE_PX.
    sharpness: mean Laplacian variance over SHARPNESS_FULL_SCALE.
    """
    frames = stats[span.start_frame : span.end_frame]
    moves = frames[1:]
    sharpness = float(np.mean([frame.sharpness for frame in frames])) if frames else 0.0
    motion = 0.0
    if moves:
        speed = float(np.mean([move.flow_px for move in moves])) / ANALYSIS_LONG_SIDE * fps
        motion = speed / MOTION_FULL_SCALE
    jitter = 0.0
    if len(moves) > 1:
        vectors = np.array([move.global_motion for move in moves])
        jitter = float(np.linalg.norm(np.diff(vectors, axis=0), axis=1).mean())
    return ShotHeuristics(
        motion_score=min(1.0, motion),
        sharpness=min(1.0, sharpness / SHARPNESS_FULL_SCALE),
        shake=min(1.0, jitter / SHAKE_FULL_SCALE_PX),
    )


def combine_quality(tagger_quality: float, heuristics: ShotHeuristics) -> float:
    """Weighted blend of the tagger's quality and the local score (sharp and steady)."""
    local = heuristics.sharpness * (1 - heuristics.shake)
    return TAGGER_WEIGHT * tagger_quality + (1 - TAGGER_WEIGHT) * local

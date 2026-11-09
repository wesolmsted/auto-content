from pathlib import Path

from engine.config import IngestSettings
from engine.db.enums import Orientation
from engine.ffmpeg import probe
from engine.ingest.frames import downscale_filter, extract_frame, sample_frame_indices
from engine.ingest.models import Shot, ShotTags
from engine.ingest.proxies import encode_proxy, vertical_filter
from engine.ingest.quality import analyze_frames, combine_quality, shot_heuristics
from engine.ingest.scenes import ShotSpan, detect_scenes, plan_shot_spans
from engine.storage.media_store import MediaKind, MediaStore
from engine.vision.tagger import VisionTagger

TAGGER_FRAME_LONG_SIDE = 768
THUMBNAIL_WIDTH = 360


def build_shots(
    video_path: Path,
    footage_file_id: int,
    tagger: VisionTagger,
    store: MediaStore,
    settings: IngestSettings,
) -> list[Shot]:
    """Split one local footage file into tagged shots (DESIGN.md §7.1 steps 3 to 6).

    Scene-split with PySceneDetect (`settings.scene_threshold`), drop shots shorter than
    `min_shot_s`, split ones longer than `max_shot_s`, score motion/quality locally, tag each
    shot with `tagger`, and write a proxy (and thumbnail) per shot into `MediaKind.SHOTS`.
    Returned shots have `id=None`; the caller stores them. Raises VisionTagError if tagging
    fails, so the whole file is retried rather than stored half-tagged; output files are named
    `footage<id>_<start frame>-<end frame>`, so a retry overwrites them. Returns [] when no
    shot survives `min_shot_s`, and raises ValueError for a file without a video stream.
    """
    info = probe(video_path)
    if info.width is None or info.height is None or not info.fps:
        raise ValueError(f"{video_path} has no usable video stream")
    fps = info.fps
    width, height = display_size(info.width, info.height, info.rotation)
    spans = plan_shot_spans(
        detect_scenes(video_path, settings.scene_threshold),
        fps,
        settings.min_shot_s,
        settings.max_shot_s,
    )
    if not spans:
        return []
    frame_stats = analyze_frames(video_path)
    proxy_filter = vertical_filter(width, height, settings.proxy_max_dim)
    thumbnail_filter = vertical_filter(width, height, THUMBNAIL_WIDTH)
    shots = []
    for span in spans:
        name = f"footage{footage_file_id}_{span.start_frame:06d}-{span.end_frame:06d}"
        tags = _tag_span(video_path, span, fps, name, (width, height), tagger, store, settings)
        heuristics = shot_heuristics(frame_stats, span, fps)
        proxy_path = encode_proxy(
            video_path, span, fps, store.path_for(MediaKind.SHOTS, f"{name}.mp4"), proxy_filter
        )
        thumbnail_path = extract_frame(
            video_path,
            sample_frame_indices(span, 1)[0],
            fps,
            store.path_for(MediaKind.SHOTS, f"{name}.jpg"),
            thumbnail_filter,
        )
        shots.append(
            Shot(
                footage_file_id=footage_file_id,
                start_s=span.start_frame / fps,
                end_s=span.end_frame / fps,
                width=width,
                height=height,
                fps=fps,
                orientation=orientation_for(width, height),
                motion_score=heuristics.motion_score,
                quality_score=combine_quality(tags.quality, heuristics),
                tags=tags,
                proxy_path=proxy_path,
                thumbnail_path=thumbnail_path,
            )
        )
    return shots


def _tag_span(
    video_path: Path,
    span: ShotSpan,
    fps: float,
    name: str,
    size: tuple[int, int],
    tagger: VisionTagger,
    store: MediaStore,
    settings: IngestSettings,
) -> ShotTags:
    """Extract the sample frames into scratch, tag them, and delete them again."""
    frame_filter = downscale_filter(*size, TAGGER_FRAME_LONG_SIDE)
    frames = [
        store.path_for(MediaKind.SCRATCH, f"{name}_frame{index}.jpg")
        for index in range(settings.frames_per_shot)
    ]
    try:
        for frame_path, frame in zip(
            frames, sample_frame_indices(span, settings.frames_per_shot), strict=True
        ):
            extract_frame(video_path, frame, fps, frame_path, frame_filter)
        return tagger.tag_shot(frames)
    finally:
        for frame_path in frames:
            frame_path.unlink(missing_ok=True)


def display_size(width: int, height: int, rotation: int) -> tuple[int, int]:
    """The size as shown: phones store portrait video as landscape plus a 90° rotation."""
    if rotation % 180 == 90:
        return height, width
    return width, height


def orientation_for(width: int, height: int) -> Orientation:
    if width > height:
        return Orientation.LANDSCAPE
    if width < height:
        return Orientation.PORTRAIT
    return Orientation.SQUARE

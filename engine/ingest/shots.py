from pathlib import Path

from engine.config import IngestSettings
from engine.ingest.models import Shot
from engine.storage.media_store import MediaStore
from engine.vision.tagger import VisionTagger


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
    fails, so the whole file is retried rather than stored half-tagged.
    """
    raise NotImplementedError

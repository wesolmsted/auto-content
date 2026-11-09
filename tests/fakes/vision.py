"""A deterministic in-memory `VisionTagger` for tests."""

from pathlib import Path

from engine.ingest.models import ShotTags
from engine.vision.tagger import VisionTagError

DEFAULT_TAGS = ShotTags(
    category="city_night",
    subject="person",
    setting="street",
    lighting="night",
    mood="energetic",
    has_face=True,
    quality=0.8,
)


class FakeVisionTagger:
    """Returns `tags` for every shot, or raises `error` if set. Records each call's frames."""

    def __init__(self, tags: ShotTags = DEFAULT_TAGS, error: VisionTagError | None = None) -> None:
        self.tags = tags
        self.error = error
        self.calls: list[list[Path]] = []

    def tag_shot(self, frames: list[Path]) -> ShotTags:
        self.calls.append(list(frames))
        if self.error is not None:
            raise self.error
        return self.tags

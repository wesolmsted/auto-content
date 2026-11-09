from pathlib import Path
from typing import Protocol

from engine.ingest.models import ShotTags


class VisionTagError(Exception):
    """The vision model failed or returned tags that don't validate. The shot is retried later."""


class VisionTagger(Protocol):
    def tag_shot(self, frames: list[Path]) -> ShotTags:
        """Tag one shot from its frames (start, middle, end). Raises VisionTagError."""
        ...

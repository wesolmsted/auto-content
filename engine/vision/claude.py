from pathlib import Path

import anthropic

from engine.config import VisionSettings
from engine.ingest.models import ShotTags


class ClaudeVisionTagger:
    """`VisionTagger` using Claude vision. The model id and tag vocabulary come from settings."""

    def __init__(self, client: anthropic.Anthropic, settings: VisionSettings) -> None:
        self.client = client
        self.settings = settings

    def tag_shot(self, frames: list[Path]) -> ShotTags:
        raise NotImplementedError

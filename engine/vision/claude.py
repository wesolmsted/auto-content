import base64
from pathlib import Path

import anthropic
from anthropic.types import ImageBlockParam, TextBlockParam
from pydantic import ValidationError

from engine.config import VisionSettings
from engine.ingest.models import ShotTags
from engine.vision.prompts import VOCABULARY_FIELDS, shot_tagging_prompt, shot_tags_schema
from engine.vision.tagger import VisionTagError


class ClaudeVisionTagger:
    """`VisionTagger` using Claude vision. The model id and tag vocabulary come from settings.

    The reply is constrained to a JSON schema built from the vocabulary, then validated again
    here; anything that doesn't validate raises VisionTagError, so no shot is half-tagged.
    """

    def __init__(self, client: anthropic.Anthropic, settings: VisionSettings) -> None:
        self.client = client
        self.settings = settings

    def tag_shot(self, frames: list[Path]) -> ShotTags:
        vocabulary = self.settings.vocabulary
        text_block: TextBlockParam = {"type": "text", "text": shot_tagging_prompt(vocabulary)}
        try:
            response = self.client.messages.create(
                model=self.settings.model,
                max_tokens=self.settings.max_tokens,
                messages=[{"role": "user", "content": [*_image_blocks(frames), text_block]}],
                output_config={
                    "format": {"type": "json_schema", "schema": shot_tags_schema(vocabulary)}
                },
            )
        except anthropic.APIError as error:
            raise VisionTagError(f"vision request failed: {error}") from error
        if response.stop_reason != "end_turn":
            raise VisionTagError(f"vision reply incomplete (stop_reason={response.stop_reason})")
        text = "".join(block.text for block in response.content if block.type == "text")
        return parse_shot_tags(text, self.settings)


def _image_blocks(frames: list[Path]) -> list[ImageBlockParam]:
    try:
        images = [frame.read_bytes() for frame in frames]
    except OSError as error:
        raise VisionTagError(f"cannot read frame: {error}") from error
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(image).decode(),
            },
        }
        for image in images
    ]


def parse_shot_tags(text: str, settings: VisionSettings) -> ShotTags:
    """Validate the model's JSON reply against `ShotTags` and the closed vocabulary."""
    try:
        tags = ShotTags.model_validate_json(text)
    except ValidationError as error:
        raise VisionTagError(f"vision reply is not valid ShotTags: {error}") from error
    for field in VOCABULARY_FIELDS:
        value = getattr(tags, field)
        if value not in getattr(settings.vocabulary, field):
            raise VisionTagError(f"vision reply has {field}={value!r}, not in the vocabulary")
    return tags

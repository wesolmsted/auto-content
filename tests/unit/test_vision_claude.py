import base64
import json
from pathlib import Path
from typing import Any, cast

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from engine.config import VisionSettings, load_settings
from engine.ingest.models import QualityIssue, ShotTags
from engine.vision.claude import ClaudeVisionTagger
from engine.vision.tagger import VisionTagError

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"

VALID_TAGS = {
    "category": "city_night",
    "subject": "person",
    "setting": "street",
    "lighting": "neon",
    "mood": "energetic",
    "has_face": True,
    "quality": 0.72,
    "issues": ["shaky"],
}


class StubMessages:
    def __init__(self, reply: Message | Exception) -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Message:
        self.requests.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class StubClient:
    def __init__(self, reply: Message | Exception) -> None:
        self.messages = StubMessages(reply)


def message(text: str, stop_reason: str = "end_turn") -> Message:
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }
    )


@pytest.fixture
def settings() -> VisionSettings:
    return load_settings(REPO_CONFIG, env_file=None).vision.model_copy(
        update={"model": "test-vision-model", "max_tokens": 777}
    )


@pytest.fixture
def frames(tmp_path: Path) -> list[Path]:
    paths = []
    for index in range(3):
        path = tmp_path / f"frame_{index}.jpg"
        path.write_bytes(b"\xff\xd8\xff fake jpeg " + bytes([index]))
        paths.append(path)
    return paths


def tag_with(reply: Message | Exception, settings: VisionSettings, frames: list[Path]) -> ShotTags:
    client = StubClient(reply)
    return ClaudeVisionTagger(cast(anthropic.Anthropic, client), settings).tag_shot(frames)


def test_valid_response_is_parsed_into_shot_tags(
    settings: VisionSettings, frames: list[Path]
) -> None:
    tags = tag_with(message(json.dumps(VALID_TAGS)), settings, frames)
    assert tags == ShotTags(
        category="city_night",
        subject="person",
        setting="street",
        lighting="neon",
        mood="energetic",
        has_face=True,
        quality=0.72,
        issues=(QualityIssue.SHAKY,),
    )


def test_request_sends_every_frame_as_an_image_and_the_vocabulary(
    settings: VisionSettings, frames: list[Path]
) -> None:
    client = StubClient(message(json.dumps(VALID_TAGS)))
    ClaudeVisionTagger(cast(anthropic.Anthropic, client), settings).tag_shot(frames)

    [request] = client.messages.requests
    assert request["model"] == "test-vision-model"
    assert request["max_tokens"] == 777
    [user_turn] = request["messages"]
    images = [block for block in user_turn["content"] if block["type"] == "image"]
    assert [image["source"]["data"] for image in images] == [
        base64.standard_b64encode(path.read_bytes()).decode() for path in frames
    ]
    assert {image["source"]["media_type"] for image in images} == {"image/jpeg"}

    prompt = " ".join(block["text"] for block in user_turn["content"] if block["type"] == "text")
    vocabulary = settings.vocabulary
    for values in (
        vocabulary.category,
        vocabulary.subject,
        vocabulary.setting,
        vocabulary.lighting,
        vocabulary.mood,
    ):
        for value in values:
            assert value in prompt

    schema = request["output_config"]["format"]["schema"]
    assert schema["properties"]["category"]["enum"] == list(vocabulary.category)
    assert schema["properties"]["mood"]["enum"] == list(vocabulary.mood)
    assert set(schema["required"]) == set(VALID_TAGS)


@pytest.mark.parametrize(
    "bad_tags",
    [
        {**VALID_TAGS, "category": "underwater"},
        {**VALID_TAGS, "lighting": "Neon"},
        {**VALID_TAGS, "mood": "city_night"},
        {**VALID_TAGS, "issues": ["grainy"]},
        {**VALID_TAGS, "quality": 1.5},
        {key: value for key, value in VALID_TAGS.items() if key != "subject"},
        {**VALID_TAGS, "extra": "field"},
    ],
    ids=[
        "unknown-category",
        "wrong-case",
        "value-from-another-field",
        "unknown-issue",
        "quality-out-of-range",
        "missing-field",
        "extra-field",
    ],
)
def test_invalid_or_out_of_vocabulary_tags_raise(
    bad_tags: dict[str, Any], settings: VisionSettings, frames: list[Path]
) -> None:
    with pytest.raises(VisionTagError):
        tag_with(message(json.dumps(bad_tags)), settings, frames)


@pytest.mark.parametrize("text", ["not json", '{"category": "nature"', "[]", ""])
def test_malformed_output_raises(text: str, settings: VisionSettings, frames: list[Path]) -> None:
    with pytest.raises(VisionTagError):
        tag_with(message(text), settings, frames)


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_incomplete_responses_raise_even_if_the_json_is_valid(
    stop_reason: str, settings: VisionSettings, frames: list[Path]
) -> None:
    with pytest.raises(VisionTagError):
        tag_with(message(json.dumps(VALID_TAGS), stop_reason), settings, frames)


def test_api_errors_become_vision_tag_errors(settings: VisionSettings, frames: list[Path]) -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    with pytest.raises(VisionTagError):
        tag_with(anthropic.APIConnectionError(request=request), settings, frames)


def test_missing_frame_file_raises(settings: VisionSettings, tmp_path: Path) -> None:
    with pytest.raises(VisionTagError):
        tag_with(message(json.dumps(VALID_TAGS)), settings, [tmp_path / "missing.jpg"])

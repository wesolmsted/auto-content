from pathlib import Path

import pytest

from engine.config import load_settings
from engine.vision.tagger import VisionTagError, VisionTagger
from tests.fakes.vision import DEFAULT_TAGS, FakeVisionTagger

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def test_fake_returns_its_tags_and_records_each_call() -> None:
    tagger: VisionTagger = FakeVisionTagger()
    frames = [Path("a.jpg"), Path("b.jpg"), Path("c.jpg")]
    assert tagger.tag_shot(frames) == DEFAULT_TAGS
    assert tagger.tag_shot(frames[:1]) == DEFAULT_TAGS
    assert isinstance(tagger, FakeVisionTagger)
    assert tagger.calls == [frames, frames[:1]]


def test_fake_returns_custom_tags() -> None:
    custom = DEFAULT_TAGS.model_copy(update={"category": "nature", "quality": 0.2})
    assert FakeVisionTagger(tags=custom).tag_shot([Path("a.jpg")]) == custom


def test_fake_raises_the_configured_error_and_still_records_the_call() -> None:
    tagger = FakeVisionTagger(error=VisionTagError("model down"))
    with pytest.raises(VisionTagError, match="model down"):
        tagger.tag_shot([Path("a.jpg")])
    assert tagger.calls == [[Path("a.jpg")]]


def test_default_tags_use_the_default_vocabulary() -> None:
    vocabulary = load_settings(REPO_CONFIG, env_file=None).vision.vocabulary
    assert DEFAULT_TAGS.category in vocabulary.category
    assert DEFAULT_TAGS.subject in vocabulary.subject
    assert DEFAULT_TAGS.setting in vocabulary.setting
    assert DEFAULT_TAGS.lighting in vocabulary.lighting
    assert DEFAULT_TAGS.mood in vocabulary.mood

"""Prompt and output schema for shot tagging. The closed vocabulary comes from config."""

from typing import Any

from engine.config import TagVocabulary
from engine.ingest.models import QualityIssue

VOCABULARY_FIELDS = ("category", "subject", "setting", "lighting", "mood")


def shot_tagging_prompt(vocabulary: TagVocabulary) -> str:
    allowed = "\n".join(
        f"- {field}: one of {', '.join(getattr(vocabulary, field))}" for field in VOCABULARY_FIELDS
    )
    issues = ", ".join(issue.value for issue in QualityIssue)
    return f"""These images are the first, middle and last frames of one shot of raw video footage.
The shot will be cut into short vertical music videos, and the tags are used to pick shots that
fit a style, so choose the closest value from each list even when none fits perfectly.

Tag the shot as a whole:
{allowed}
- has_face: true if a human face is clearly visible in any frame
- quality: 0 to 1, how usable the shot looks in a polished video (sharpness, exposure,
  steadiness, composition); 0.5 is ordinary phone footage
- issues: any of {issues} that clearly apply, or an empty list

Reply with only the JSON object."""


def shot_tags_schema(vocabulary: TagVocabulary) -> dict[str, Any]:
    properties: dict[str, Any] = {
        field: {"type": "string", "enum": list(getattr(vocabulary, field))}
        for field in VOCABULARY_FIELDS
    }
    properties["has_face"] = {"type": "boolean"}
    properties["quality"] = {"type": "number"}
    properties["issues"] = {
        "type": "array",
        "items": {"type": "string", "enum": [issue.value for issue in QualityIssue]},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }

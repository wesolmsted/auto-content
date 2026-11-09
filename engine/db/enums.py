"""Enums persisted in the database. Stored as their string values (see `StrEnumType`)."""

from enum import StrEnum


class FootageStatus(StrEnum):
    NEW = "new"
    DOWNLOADED = "downloaded"
    SPLIT = "split"
    TAGGED = "tagged"
    DONE = "done"
    DUPLICATE = "duplicate"
    FAILED = "failed"


class RenderStatus(StrEnum):
    RENDERING = "rendering"
    READY = "ready"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class PostStatus(StrEnum):
    AWAITING_POST = "awaiting_post"
    MATCHED = "matched"
    NOT_POSTED = "not_posted"


class MetricImportStatus(StrEnum):
    NEW = "new"
    IMPORTED = "imported"
    FAILED = "failed"


class StyleReferenceStatus(StrEnum):
    NEW = "new"
    DOWNLOADED = "downloaded"
    ANALYZED = "analyzed"
    FAILED = "failed"


class Platform(StrEnum):
    IG_TRIAL = "ig_trial"
    TIKTOK = "tiktok"


class MatchMethod(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class MetricCheckpoint(StrEnum):
    H24 = "24h"
    H72 = "72h"
    EXPORT = "export"


class MetricSource(StrEnum):
    API = "api"
    EXPORT = "export"


class Orientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SQUARE = "square"


class SnippetType(StrEnum):
    HOOK = "hook"
    DROP = "drop"
    INTRO_BUILD = "intro_build"


class RejectReason(StrEnum):
    OFF_BRAND = "off_brand"
    BAD_SHOT = "bad_shot"
    BAD_TIMING = "bad_timing"
    BAD_TEXT = "bad_text"
    BORING = "boring"
    OTHER = "other"


INTERNAL_SCORE_PLATFORM = "internal"
"""`variable_scores.platform` value for the taste prior from hand-off approvals (DESIGN.md §7.7)."""

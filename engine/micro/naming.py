"""How a micro source is recognized in the footage inbox (DESIGN.md §7.10)."""

from pathlib import PurePath

MICRO_STEM_SUFFIX = "_micro"
SIDECAR_SUFFIX = ".txt"


def is_micro_video_name(name: str) -> bool:
    """A file stem ending in `_micro` (any case) with a non-sidecar extension."""
    path = PurePath(name)
    return path.suffix not in {"", SIDECAR_SUFFIX} and path.stem.lower().endswith(MICRO_STEM_SUFFIX)


def sidecar_name_for(video_name: str) -> str:
    return PurePath(video_name).with_suffix(SIDECAR_SUFFIX).name

"""How a micro set is recognized in the footage inbox (DESIGN.md §7.10)."""

MICRO_FOLDER_SUFFIX = "_micro"
SIDECAR_SUFFIX = ".txt"


def is_micro_folder_name(name: str) -> bool:
    """A folder name ending in `_micro`, ignoring case and trailing whitespace."""
    return name.rstrip().lower().endswith(MICRO_FOLDER_SUFFIX)

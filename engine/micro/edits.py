import random

from engine.config import MicroSettings
from engine.micro.models import MicroEdit


def sample_edit(rng: random.Random, settings: MicroSettings, text: str) -> MicroEdit:
    """Draw a fresh set of small edits within the `settings` ranges (DESIGN.md §7.10). Pure."""
    raise NotImplementedError


def output_duration_s(edit: MicroEdit, source_duration_s: float) -> float:
    """(source duration - trims) / speed."""
    raise NotImplementedError

import random

from engine.config import MicroSettings
from engine.micro.models import MicroEdit, MicroTextPosition


def sample_edit(rng: random.Random, settings: MicroSettings, text: str) -> MicroEdit:
    """Draw a fresh set of small edits within the `settings` ranges (DESIGN.md §7.10). Pure."""
    speed_sign = rng.choice((-1, 1))
    return MicroEdit(
        trim_start_s=rng.uniform(*settings.trim_s),
        trim_end_s=rng.uniform(*settings.trim_s),
        speed=1 + speed_sign * rng.uniform(*settings.speed_change),
        zoom=rng.uniform(*settings.zoom),
        crop_x=rng.random(),
        crop_y=rng.random(),
        mirror=rng.random() < settings.mirror_probability,
        brightness=_plus_or_minus(rng, settings.brightness),
        contrast=rng.uniform(*settings.contrast),
        saturation=rng.uniform(*settings.saturation),
        hue_degrees=_plus_or_minus(rng, settings.hue_degrees),
        text=text,
        text_position=rng.choice(list(MicroTextPosition)),
        text_size_fraction=settings.text_size_fraction
        * (1 + _plus_or_minus(rng, settings.text_size_jitter)),
        text_y_offset=_plus_or_minus(rng, settings.text_y_jitter),
    )


def output_duration_s(edit: MicroEdit, source_duration_s: float) -> float:
    """(source duration - trims) / speed."""
    return (source_duration_s - edit.trim_start_s - edit.trim_end_s) / edit.speed


def _plus_or_minus(rng: random.Random, amount: float) -> float:
    return rng.uniform(-amount, amount)

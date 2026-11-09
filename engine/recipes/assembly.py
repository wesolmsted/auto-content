from collections.abc import Sequence

from engine.ingest.models import Shot
from engine.recipes.models import Recipe, Timeline
from engine.snippets.models import BeatGrid


def assemble(
    recipe: Recipe,
    shots: Sequence[Shot],
    beat_grid: BeatGrid,
    text: str | None = None,
) -> Timeline:
    """Pure: cut `shots` (in the order from `select_shots`) to the beat grid (DESIGN.md §7.3).

    Cut points follow `recipe.cut_rate`, with a cut on `beat_grid.drop_s` when
    `recipe.hard_cut_on_drop`; total length is `recipe.length_s` snapped to the nearest beat.
    A shot shorter than its slot is skipped; no shot is used twice. Effects are keyed to
    beat/downbeat times. `text` is the on-screen text for `recipe.on_screen_text` (unused for
    `none`).
    """
    raise NotImplementedError

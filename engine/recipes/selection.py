import random
from collections.abc import Sequence
from datetime import datetime

from engine.config import RecipeSettings
from engine.ingest.models import Shot
from engine.recipes.models import Recipe
from engine.snippets.models import BeatGrid


def select_shots(
    recipe: Recipe,
    library: Sequence[Shot],
    beat_grid: BeatGrid,
    rng: random.Random,
    settings: RecipeSettings,
    now: datetime,
) -> list[Shot]:
    """Pure: the shots for one video, in timeline order (DESIGN.md §7.3).

    Filters by `recipe.footage_category` (any category for `mixed`) and
    `settings.quality_floor`, excludes shots used within `settings.shot_cooldown_days` of
    `now`, and puts the best shot for `recipe.opening_shot` first. Deterministic for a given
    `rng` state.
    """
    raise NotImplementedError

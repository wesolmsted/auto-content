import random
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from engine.config import RecipeSettings
from engine.ingest.models import Shot
from engine.recipes.models import CutRate, FootageCategory, OpeningShot, Recipe
from engine.recipes.slots import NotEnoughShotsError, fit_long_shots, plan_slots
from engine.snippets.models import BeatGrid

SCENIC_CATEGORIES = {"nature"}
SCENIC_SETTINGS = {"nature", "beach", "rooftop"}
SCENIC_SUBJECTS = {"landscape", "architecture"}

OpeningRank = Callable[[Shot], tuple[float, ...]]


def select_shots(
    recipe: Recipe,
    library: Sequence[Shot],
    beat_grid: BeatGrid,
    rng: random.Random,
    settings: RecipeSettings,
    now: datetime,
) -> list[Shot]:
    """Pure: one distinct shot per timeline slot, in timeline order (DESIGN.md §7.3).

    Filters by `recipe.footage_orientation` (a render never mixes orientations),
    `recipe.footage_category` (any category for `mixed`) and `settings.quality_floor`, and
    excludes shots used within `settings.shot_cooldown_days` of `now`. Slot 0 gets the best
    shot for `recipe.opening_shot` that is long enough for it; the other slots, longest first,
    each take a random remaining shot that is long enough. `one_long_shot` ignores
    `opening_shot` and takes the longest shots, longest first (see `fit_long_shots`). Raises
    `NotEnoughShotsError` when the library can't fill every slot. Deterministic for a given
    `rng` state.
    """
    pool = [shot for shot in library if _is_eligible(shot, recipe, settings, now)]
    eligible_count = len(pool)
    rng.shuffle(pool)
    if recipe.cut_rate is CutRate.ONE_LONG_SHOT:
        pool.sort(key=lambda shot: shot.duration_s, reverse=True)
        fitted = fit_long_shots(recipe, beat_grid, [shot.duration_s for shot in pool])
        return [pool[index] for index, _ in fitted]

    slots = plan_slots(recipe, beat_grid)
    ranked_openers = sorted(pool, key=_opening_rank(recipe.opening_shot), reverse=True)

    # Longest slots first: any shot that fits a slot also fits every shorter one.
    later_slots = sorted(
        range(1, len(slots)), key=lambda index: slots[index].length_s, reverse=True
    )
    chosen: dict[int, Shot] = {}
    for index in [0, *later_slots]:
        candidates = ranked_openers if index == 0 else pool
        slot = slots[index]
        shot = next((shot for shot in candidates if shot.duration_s >= slot.length_s), None)
        if shot is None:
            raise NotEnoughShotsError(
                f"recipe {recipe.id}: {eligible_count} eligible shots can't fill "
                f"{len(slots)} slots (none left at least {slot.length_s:.2f} s long "
                f"for slot {index + 1})"
            )
        pool.remove(shot)
        chosen[index] = shot
    return [chosen[index] for index in range(len(slots))]


def _is_eligible(shot: Shot, recipe: Recipe, settings: RecipeSettings, now: datetime) -> bool:
    if shot.orientation != recipe.footage_orientation:
        return False
    if (
        recipe.footage_category is not FootageCategory.MIXED
        and shot.tags.category != recipe.footage_category
    ):
        return False
    if shot.quality_score < settings.quality_floor:
        return False
    cooldown = timedelta(days=settings.shot_cooldown_days)
    return shot.last_used_at is None or now - shot.last_used_at >= cooldown


def _opening_rank(opening_shot: OpeningShot) -> OpeningRank:
    match opening_shot:
        case OpeningShot.MOST_MOTION:
            return lambda shot: (shot.motion_score,)
        case OpeningShot.PERSON_FACE:
            return lambda shot: (shot.tags.has_face, shot.quality_score)
        case OpeningShot.SCENIC:
            return lambda shot: (_is_scenic(shot), shot.quality_score)
        case OpeningShot.TEXT_LED:
            return lambda shot: (shot.tags.mood == "calm", -shot.motion_score)


def _is_scenic(shot: Shot) -> bool:
    return (
        shot.tags.category in SCENIC_CATEGORIES
        or shot.tags.setting in SCENIC_SETTINGS
        or shot.tags.subject in SCENIC_SUBJECTS
    )

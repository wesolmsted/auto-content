import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from engine.config import RecipeSettings, load_settings
from engine.db.enums import Orientation
from engine.ingest.models import Shot, ShotTags
from engine.recipes.models import Recipe
from engine.recipes.selection import select_shots
from engine.recipes.slots import NotEnoughShotsError
from engine.snippets.models import BeatGrid

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 8, 6, 0, tzinfo=UTC)


@pytest.fixture
def settings() -> RecipeSettings:
    return load_settings(REPO_ROOT / "config", env_file=None).recipes.model_copy(
        update={"quality_floor": 0.4, "shot_cooldown_days": 3}
    )


def make_grid(drop_s: float | None = 8.0) -> BeatGrid:
    """120 BPM over 20 s: beats every 0.5 s from 0, downbeats every 2 s."""
    beats = tuple(index * 0.5 for index in range(40))
    return BeatGrid(bpm=120.0, beats=beats, downbeats=beats[::4], drop_s=drop_s, duration_s=20.0)


def make_recipe(**overrides: Any) -> Recipe:
    params: dict[str, Any] = {
        "cut_rate": "downbeats",
        "hard_cut_on_drop": True,
        "opening_shot": "most_motion",
        "footage_category": "mixed",
        "footage_orientation": "portrait",
        "length_s": 4,
        "on_screen_text": "none",
        "snippet_type": "drop",
        "look": "natural",
        "effects": [],
    }
    return Recipe.model_validate(params | overrides)


def make_shot(
    shot_id: int,
    *,
    duration_s: float = 5.0,
    motion: float = 0.5,
    quality: float = 0.8,
    category: str = "city_night",
    subject: str = "person",
    setting: str = "street",
    mood: str = "energetic",
    has_face: bool = False,
    last_used_at: datetime | None = None,
    orientation: Orientation = Orientation.PORTRAIT,
) -> Shot:
    return Shot(
        id=shot_id,
        footage_file_id=1,
        start_s=0.0,
        end_s=duration_s,
        width=1080,
        height=1920,
        fps=30.0,
        orientation=orientation,
        motion_score=motion,
        quality_score=quality,
        tags=ShotTags(
            category=category,
            subject=subject,
            setting=setting,
            lighting="night",
            mood=mood,
            has_face=has_face,
            quality=quality,
        ),
        proxy_path=Path(f"/media/shots/{shot_id}.mp4"),
        last_used_at=last_used_at,
    )


def ids(shots: list[Shot]) -> list[int | None]:
    return [shot.id for shot in shots]


def select(
    library: list[Shot], settings: RecipeSettings, seed: int = 0, **recipe: Any
) -> list[Shot]:
    return select_shots(
        make_recipe(**recipe), library, make_grid(), random.Random(seed), settings, NOW
    )


def test_returns_one_distinct_shot_per_slot(settings: RecipeSettings) -> None:
    library = [make_shot(shot_id) for shot_id in range(1, 21)]
    chosen = select(library, settings, cut_rate="every_2_beats", length_s=7)
    assert len(chosen) == 7
    assert len(set(ids(chosen))) == 7


def test_filters_by_footage_category(settings: RecipeSettings) -> None:
    library = [
        make_shot(1, category="nature"),
        make_shot(2, category="city_night"),
        make_shot(3, category="nature"),
        make_shot(4, category="city_night"),
    ]
    chosen = select(library, settings, footage_category="city_night")
    assert sorted(ids(chosen)) == [2, 4]


def test_mixed_accepts_any_category(settings: RecipeSettings) -> None:
    library = [make_shot(1, category="nature"), make_shot(2, category="crowd")]
    assert sorted(ids(select(library, settings, footage_category="mixed"))) == [1, 2]


@pytest.mark.parametrize(
    ("footage_orientation", "expected_ids"),
    [("landscape", [1, 3]), ("portrait", [2, 4])],
)
def test_never_mixes_orientations(
    settings: RecipeSettings, footage_orientation: str, expected_ids: list[int]
) -> None:
    library = [
        make_shot(1, orientation=Orientation.LANDSCAPE),
        make_shot(2, orientation=Orientation.PORTRAIT),
        make_shot(3, orientation=Orientation.LANDSCAPE),
        make_shot(4, orientation=Orientation.PORTRAIT),
        make_shot(5, orientation=Orientation.SQUARE),
    ]
    chosen = select(library, settings, footage_orientation=footage_orientation)
    assert sorted(ids(chosen)) == expected_ids


def test_raises_when_too_few_shots_have_the_orientation(settings: RecipeSettings) -> None:
    library = [make_shot(shot_id, orientation=Orientation.LANDSCAPE) for shot_id in range(1, 6)]
    library.append(make_shot(6, orientation=Orientation.PORTRAIT))
    with pytest.raises(NotEnoughShotsError, match="1 eligible"):
        select(library, settings, footage_orientation="portrait")


def test_shots_below_the_quality_floor_are_excluded(settings: RecipeSettings) -> None:
    library = [make_shot(1, quality=0.39), make_shot(2, quality=0.4), make_shot(3, quality=0.9)]
    assert sorted(ids(select(library, settings))) == [2, 3]


def test_shots_used_within_the_cooldown_are_excluded(settings: RecipeSettings) -> None:
    library = [
        make_shot(1, last_used_at=NOW - timedelta(days=2, hours=23)),
        make_shot(2, last_used_at=NOW - timedelta(days=3)),
        make_shot(3),
    ]
    assert sorted(ids(select(library, settings))) == [2, 3]


def test_zero_cooldown_allows_recently_used_shots(settings: RecipeSettings) -> None:
    no_cooldown = settings.model_copy(update={"shot_cooldown_days": 0})
    library = [make_shot(1, last_used_at=NOW), make_shot(2, last_used_at=NOW)]
    assert sorted(ids(select(library, no_cooldown))) == [1, 2]


def test_shots_too_short_for_every_slot_are_not_chosen(settings: RecipeSettings) -> None:
    library = [make_shot(1, duration_s=1.5), make_shot(2), make_shot(3, duration_s=1.9)]
    library.append(make_shot(4, duration_s=2.0))
    assert sorted(ids(select(library, settings))) == [2, 4]


def test_most_motion_opens_with_the_highest_motion_shot(settings: RecipeSettings) -> None:
    library = [make_shot(1, motion=0.2), make_shot(2, motion=0.9), make_shot(3, motion=0.5)]
    for seed in range(5):
        assert ids(select(library, settings, seed=seed, opening_shot="most_motion"))[0] == 2


def test_person_face_opens_with_the_best_quality_face(settings: RecipeSettings) -> None:
    library = [
        make_shot(1, quality=0.99),
        make_shot(2, has_face=True, quality=0.6),
        make_shot(3, has_face=True, quality=0.8),
    ]
    for seed in range(5):
        assert ids(select(library, settings, seed=seed, opening_shot="person_face"))[0] == 3


def test_scenic_opens_with_a_scenic_shot(settings: RecipeSettings) -> None:
    library = [
        make_shot(1, quality=0.99),
        make_shot(2, setting="nature", quality=0.5),
        make_shot(3, setting="beach", quality=0.7),
        make_shot(4, subject="landscape", quality=0.6),
    ]
    for seed in range(5):
        chosen = select(library, settings, seed=seed, opening_shot="scenic", length_s=8)
        assert ids(chosen)[0] == 3


def test_text_led_opens_with_a_calm_low_motion_shot(settings: RecipeSettings) -> None:
    library = [
        make_shot(1, mood="calm", motion=0.6),
        make_shot(2, mood="energetic", motion=0.05),
        make_shot(3, mood="calm", motion=0.1),
    ]
    for seed in range(5):
        assert ids(select(library, settings, seed=seed, opening_shot="text_led"))[0] == 3


def test_the_opener_must_be_long_enough_for_the_first_slot(settings: RecipeSettings) -> None:
    library = [
        make_shot(1, motion=0.9, duration_s=1.0),
        make_shot(2, motion=0.7),
        make_shot(3, motion=0.1),
    ]
    assert ids(select(library, settings, opening_shot="most_motion"))[0] == 2


def test_long_shots_are_kept_for_long_slots(settings: RecipeSettings) -> None:
    # Slots are 2, 2, 2, 2, 0.25 (8.0 to the drop at 8.25), 1.75 and 2 s.
    library = [make_shot(shot_id, duration_s=2.0) for shot_id in range(1, 7)]
    library.append(make_shot(7, duration_s=0.3))
    grid = make_grid(drop_s=8.25)
    recipe = make_recipe(length_s=12)
    for seed in range(20):
        chosen = select_shots(recipe, library, grid, random.Random(seed), settings, NOW)
        assert ids(chosen)[4] == 7


def test_same_seed_gives_the_same_shots_and_seeds_vary_the_order(
    settings: RecipeSettings,
) -> None:
    library = [make_shot(shot_id) for shot_id in range(1, 31)]
    orders = {
        tuple(ids(select(library, settings, seed=seed, cut_rate="every_beat"))) for seed in range(5)
    }
    assert ids(select(library, settings, seed=7)) == ids(select(library, settings, seed=7))
    assert len(orders) > 1


def test_raises_when_the_library_cannot_fill_the_timeline(settings: RecipeSettings) -> None:
    library = [make_shot(1), make_shot(2, quality=0.1), make_shot(3, category="nature")]
    with pytest.raises(NotEnoughShotsError, match="1 eligible"):
        select(library, settings, footage_category="city_night")


def test_raises_on_an_empty_library(settings: RecipeSettings) -> None:
    with pytest.raises(NotEnoughShotsError):
        select([], settings)

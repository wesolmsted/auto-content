"""Property tests for the timeline invariants in DESIGN.md §11."""

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from engine.config import load_settings
from engine.db.enums import Orientation, SnippetType
from engine.ingest.models import Shot, ShotTags
from engine.recipes.assembly import assemble
from engine.recipes.models import (
    CutRate,
    Effect,
    FootageCategory,
    Look,
    OnScreenText,
    OpeningShot,
    Recipe,
    Timeline,
)
from engine.recipes.selection import select_shots
from engine.recipes.slots import MIN_SEGMENT_S, NotEnoughShotsError
from engine.snippets.models import BeatGrid

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPE_SETTINGS = load_settings(REPO_ROOT / "config", env_file=None).recipes
FRAME_S = 1 / 30
EPSILON = 1e-9
NOW = datetime(2026, 11, 8, 6, 0, tzinfo=UTC)
DECOY_DURATION_S = MIN_SEGMENT_S / 2


@st.composite
def beat_grids(draw: st.DrawFn) -> BeatGrid:
    """Realistic grids: 60-180 BPM with up to ±20% jitter, any phase, any drop or none."""
    bpm = draw(st.floats(60, 180))
    spacing = 60 / bpm
    duration_s = draw(st.floats(4, 30))
    offset = draw(st.floats(0, spacing * 0.9))
    count = int((duration_s - offset) / spacing) + 1
    jitters = draw(st.lists(st.floats(-0.2, 0.2), min_size=count, max_size=count))
    candidates = [offset + (index + jitter) * spacing for index, jitter in enumerate(jitters)]
    beats = tuple(beat for beat in candidates if 0 <= beat <= duration_s)
    downbeats = beats[draw(st.integers(0, 3)) :: 4]
    drop_choices = st.none() | st.floats(0, duration_s)
    if downbeats:
        drop_choices |= st.sampled_from(downbeats)
    return BeatGrid(
        bpm=bpm,
        beats=beats,
        downbeats=downbeats,
        drop_s=draw(drop_choices),
        duration_s=duration_s,
    )


recipes = st.builds(
    Recipe,
    cut_rate=st.sampled_from(CutRate),
    hard_cut_on_drop=st.booleans(),
    opening_shot=st.sampled_from(OpeningShot),
    footage_category=st.sampled_from(FootageCategory),
    footage_orientation=st.just(Orientation.PORTRAIT),
    length_s=st.integers(1, 30),
    on_screen_text=st.sampled_from(OnScreenText),
    snippet_type=st.sampled_from(SnippetType),
    look=st.sampled_from(Look),
    effects=st.lists(st.sampled_from(Effect), unique=True).map(tuple),
)

categories = st.sampled_from(["city_night", "nature", "studio_performance", "crowd", "other"])


def make_shot(
    shot_id: int,
    duration_s: float,
    *,
    category: str = "city_night",
    motion: float = 0.5,
    quality: float = 0.8,
    has_face: bool = False,
    mood: str = "energetic",
    last_used_at: datetime | None = None,
) -> Shot:
    return Shot(
        id=shot_id,
        footage_file_id=1,
        start_s=0.0,
        end_s=duration_s,
        width=1080,
        height=1920,
        fps=30.0,
        orientation=Orientation.PORTRAIT,
        motion_score=motion,
        quality_score=quality,
        tags=ShotTags(
            category=category,
            subject="person",
            setting="street",
            lighting="night",
            mood=mood,
            has_face=has_face,
            quality=quality,
        ),
        proxy_path=Path(f"/media/shots/{shot_id}.mp4"),
        last_used_at=last_used_at,
    )


@st.composite
def shot_lists(draw: st.DrawFn, grid: BeatGrid) -> list[Shot]:
    """Enough shots that each fit any slot, shuffled with decoys too short for any slot."""
    long_count = len(grid.beats) + 3
    durations = [grid.duration_s + draw(st.floats(0, 5)) for _ in range(long_count)] + [
        DECOY_DURATION_S
    ] * draw(st.integers(0, 5))
    shots = [make_shot(index + 1, duration) for index, duration in enumerate(durations)]
    return draw(st.permutations(shots))


def expected_duration(recipe: Recipe, grid: BeatGrid) -> float:
    return min(
        (beat for beat in grid.beats if beat > 0), key=lambda beat: abs(beat - recipe.length_s)
    )


def allowed_cuts(recipe: Recipe, grid: BeatGrid) -> set[float]:
    grid_cuts = {
        CutRate.EVERY_BEAT: grid.beats,
        CutRate.EVERY_2_BEATS: grid.beats[::2],
        CutRate.DOWNBEATS: grid.downbeats,
        CutRate.ONE_LONG_SHOT: (),
    }[recipe.cut_rate]
    allowed = set(grid_cuts)
    if recipe.hard_cut_on_drop and grid.drop_s is not None:
        allowed.add(grid.drop_s)
    return allowed


def cut_times(timeline: Timeline) -> list[float]:
    return [segment.output_start_s for segment in timeline.segments[1:]]


@st.composite
def assembly_cases(draw: st.DrawFn) -> tuple[Recipe, BeatGrid, list[Shot], str | None]:
    grid = draw(beat_grids())
    return draw(recipes), grid, draw(shot_lists(grid)), draw(st.none() | st.text(min_size=1))


@settings(max_examples=300)
@given(assembly_cases())
def test_total_duration_is_the_length_snapped_to_the_nearest_beat(
    case: tuple[Recipe, BeatGrid, list[Shot], str | None],
) -> None:
    recipe, grid, shots, text = case
    timeline = assemble(recipe, shots, grid, text)
    assert abs(timeline.duration_s - expected_duration(recipe, grid)) <= FRAME_S


@settings(max_examples=300)
@given(assembly_cases())
def test_every_cut_is_on_the_recipe_grid_or_at_the_drop(
    case: tuple[Recipe, BeatGrid, list[Shot], str | None],
) -> None:
    recipe, grid, shots, text = case
    allowed = allowed_cuts(recipe, grid)
    for cut in cut_times(assemble(recipe, shots, grid, text)):
        assert any(abs(cut - time) <= EPSILON for time in allowed), cut


@settings(max_examples=300)
@given(assembly_cases())
def test_cuts_at_the_drop_when_required(
    case: tuple[Recipe, BeatGrid, list[Shot], str | None],
) -> None:
    recipe, grid, shots, text = case
    timeline = assemble(recipe, shots, grid, text)
    drop_s = grid.drop_s
    if (
        recipe.hard_cut_on_drop
        and drop_s is not None
        and MIN_SEGMENT_S <= drop_s <= timeline.duration_s - MIN_SEGMENT_S
    ):
        assert drop_s in cut_times(timeline)


@settings(max_examples=300)
@given(assembly_cases())
def test_no_shot_is_reused_and_too_short_shots_are_never_used(
    case: tuple[Recipe, BeatGrid, list[Shot], str | None],
) -> None:
    recipe, grid, shots, text = case
    timeline = assemble(recipe, shots, grid, text)
    decoys = {shot.id for shot in shots if shot.duration_s == DECOY_DURATION_S}
    assert len(set(timeline.shot_ids)) == len(timeline.shot_ids)
    assert not decoys & set(timeline.shot_ids)


@settings(max_examples=300)
@given(assembly_cases())
def test_segments_are_contiguous_and_within_their_shot(
    case: tuple[Recipe, BeatGrid, list[Shot], str | None],
) -> None:
    recipe, grid, shots, text = case
    timeline = assemble(recipe, shots, grid, text)
    by_id = {shot.id: shot for shot in shots}
    assert timeline.segments[0].output_start_s == 0.0
    assert timeline.segments[-1].output_end_s == timeline.duration_s
    for previous, segment in zip(timeline.segments, timeline.segments[1:], strict=False):
        assert segment.output_start_s == previous.output_end_s
    for segment in timeline.segments:
        shot = by_id[segment.shot_id]
        assert segment.source_path == shot.proxy_path
        assert 0 <= segment.source_in_s < segment.source_out_s <= shot.duration_s


@settings(max_examples=300)
@given(assembly_cases())
def test_effects_and_texts_lie_within_the_video(
    case: tuple[Recipe, BeatGrid, list[Shot], str | None],
) -> None:
    recipe, grid, shots, text = case
    timeline = assemble(recipe, shots, grid, text)
    assert {event.effect for event in timeline.effects} <= set(recipe.effects)
    for event in timeline.effects:
        assert 0 <= event.start_s < timeline.duration_s
        assert event.start_s + event.duration_s <= timeline.duration_s + EPSILON
    wants_text = recipe.on_screen_text is not OnScreenText.NONE and bool(text and text.strip())
    assert len(timeline.texts) == (1 if wants_text else 0)


@st.composite
def libraries(draw: st.DrawFn) -> list[Shot]:
    count = draw(st.integers(0, 60))
    return [
        make_shot(
            shot_id,
            draw(st.floats(0.2, 10)),
            category=draw(categories),
            motion=draw(st.floats(0, 1)),
            quality=draw(st.floats(0, 1)),
            has_face=draw(st.booleans()),
            mood=draw(st.sampled_from(["calm", "energetic"])),
            last_used_at=draw(st.none() | st.just(NOW - timedelta(days=draw(st.integers(0, 6))))),
        )
        for shot_id in range(1, count + 1)
    ]


@settings(max_examples=300)
@given(recipes, beat_grids(), libraries(), st.integers(0, 2**32))
def test_selected_shots_are_eligible_and_assemble_in_selection_order(
    recipe: Recipe, grid: BeatGrid, library: list[Shot], seed: int
) -> None:
    try:
        chosen = select_shots(recipe, library, grid, random.Random(seed), RECIPE_SETTINGS, NOW)
    except NotEnoughShotsError:
        return
    cooldown = timedelta(days=RECIPE_SETTINGS.shot_cooldown_days)
    for shot in chosen:
        assert recipe.footage_category in (FootageCategory.MIXED, shot.tags.category)
        assert shot.quality_score >= RECIPE_SETTINGS.quality_floor
        assert shot.last_used_at is None or NOW - shot.last_used_at >= cooldown
    timeline = assemble(recipe, chosen, grid)
    assert timeline.shot_ids == [shot.id for shot in chosen]


@settings(max_examples=100)
@given(recipes, beat_grids(), libraries(), st.integers(0, 2**32))
def test_selection_is_deterministic_for_a_seed(
    recipe: Recipe, grid: BeatGrid, library: list[Shot], seed: int
) -> None:
    def run() -> list[int | None] | str:
        try:
            chosen = select_shots(recipe, library, grid, random.Random(seed), RECIPE_SETTINGS, NOW)
        except NotEnoughShotsError as error:
            return str(error)
        return [shot.id for shot in chosen]

    assert run() == run()

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.config import Settings, load_settings
from engine.db.enums import Orientation, Platform, RenderStatus, SnippetType
from engine.db.models import FootageFileRow, RecipeRow, RenderRow, ShotRow, SnippetRow, SongRow
from engine.recipes.batch import plan_batch
from engine.recipes.models import Recipe, RenderPlan

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 8, 14, 0, tzinfo=UTC)
SONG_TITLE = "Night Drive"


@pytest.fixture
def settings() -> Settings:
    return load_settings(REPO_ROOT / "config", env_file=None)


@pytest.fixture
def fixed(settings: Settings) -> tuple[Recipe, Recipe, Recipe]:
    """The default config's recipes: every_2_beats/drop, every_beat/drop, downbeats/hook."""
    first, second, third = settings.recipes.fixed
    return first, second, third


@pytest.fixture
def seeded(db_session: Session) -> dict[str, int]:
    """A 120 BPM song with active drop and hook snippets, an inactive one, and 60 tagged shots."""
    beats = [index * 0.5 for index in range(120)]
    song = SongRow(
        title=SONG_TITLE,
        audio_path="songs/night_drive.wav",
        duration_s=60.0,
        bpm=120.0,
        beats_json=beats,
        downbeats_json=beats[::4],
        analyzed_at=NOW,
    )
    footage = FootageFileRow(drive_file_id="drive-1", name="clip.mp4")
    db_session.add_all([song, footage])
    db_session.flush()
    drop = SnippetRow(
        song_id=song.id,
        label="chorus",
        snippet_type=SnippetType.DROP,
        start_s=10.0,
        end_s=32.0,
        drop_s=18.0,
    )
    hook = SnippetRow(
        song_id=song.id, label="hook", snippet_type=SnippetType.HOOK, start_s=30.0, end_s=55.0
    )
    inactive = SnippetRow(
        song_id=song.id,
        label="old",
        snippet_type=SnippetType.DROP,
        start_s=0.0,
        end_s=20.0,
        active=False,
    )
    db_session.add_all([drop, hook, inactive])
    db_session.add_all(make_shot_row(footage.id, index) for index in range(60))
    db_session.add(make_shot_row(footage.id, 99, category=None))
    db_session.flush()
    return {"drop": drop.id, "hook": hook.id, "inactive": inactive.id, "footage": footage.id}


def make_shot_row(footage_file_id: int, index: int, **overrides: Any) -> ShotRow:
    values: dict[str, Any] = {
        "footage_file_id": footage_file_id,
        "start_s": index * 10.0,
        "end_s": index * 10.0 + 5.0,
        "duration_s": 5.0,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "orientation": Orientation.PORTRAIT,
        "category": "city_night",
        "subject": "person",
        "setting": "street",
        "lighting": "night",
        "mood": "energetic",
        "motion_score": (index % 10) / 10,
        "quality_score": 0.8,
        "has_face": False,
        "tags_json": {},
        "proxy_path": f"/data/media/shots/{index}.mp4",
    }
    return ShotRow(**(values | overrides))


def add_render(
    session: Session,
    recipe: Recipe,
    snippet_id: int,
    created_at: datetime,
    targets: list[str],
    status: RenderStatus = RenderStatus.READY,
) -> None:
    if session.get(RecipeRow, recipe.id) is None:
        session.add(RecipeRow(id=recipe.id, params_json=recipe.canonical_params()))
    session.add(
        RenderRow(
            recipe_id=recipe.id,
            snippet_id=snippet_id,
            timeline_json={},
            targets_json=targets,
            status=status,
            created_at=created_at,
        )
    )
    session.flush()


def shot_ids(plans: list[RenderPlan]) -> list[int]:
    return [shot_id for render_plan in plans for shot_id in render_plan.timeline.shot_ids]


def with_cadence(settings: Settings, **cadence: Any) -> Settings:
    return settings.model_copy(update={"cadence": settings.cadence.model_copy(update=cadence)})


def plan(session: Session, settings: Settings, n: int = 4, seed: int = 1) -> list[RenderPlan]:
    return plan_batch(session, settings, random.Random(seed), NOW, n)


def test_plans_n_renders_from_fixed_recipes_and_matching_active_snippets(
    db_session: Session, settings: Settings, seeded: dict[str, int]
) -> None:
    plans = plan(db_session, settings, n=4)
    recipes_by_id = {recipe.id: recipe for recipe in settings.recipes.fixed}
    snippet_types = {seeded["drop"]: SnippetType.DROP, seeded["hook"]: SnippetType.HOOK}
    assert len(plans) == 4
    for render_plan in plans:
        recipe = recipes_by_id[render_plan.recipe_id]
        assert snippet_types[render_plan.snippet_id] is recipe.snippet_type
        assert render_plan.timeline.look is recipe.look


def test_timelines_follow_the_snippet_beat_grid_and_drop(
    db_session: Session, settings: Settings, fixed: tuple[Recipe, ...], seeded: dict[str, int]
) -> None:
    only_first = settings.model_copy(
        update={"recipes": settings.recipes.model_copy(update={"fixed": fixed[:1]})}
    )
    [render_plan] = plan(db_session, only_first, n=1)
    cuts = [segment.output_start_s for segment in render_plan.timeline.segments[1:]]
    assert render_plan.snippet_id == seeded["drop"]
    assert render_plan.timeline.duration_s == 12.0
    assert cuts == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]


def test_untagged_shots_are_never_planned(
    db_session: Session, settings: Settings, seeded: dict[str, int]
) -> None:
    untagged = db_session.scalars(select(ShotRow.id).where(ShotRow.category.is_(None))).one()
    plans = plan(db_session, settings)
    assert len(plans) == 4
    assert untagged not in shot_ids(plans)


def test_no_shot_is_used_twice_in_one_batch(
    db_session: Session, settings: Settings, seeded: dict[str, int]
) -> None:
    used = shot_ids(plan(db_session, settings))
    assert len(used) > 30
    assert len(used) == len(set(used))


def test_recently_used_shots_are_skipped(
    db_session: Session, settings: Settings, seeded: dict[str, int]
) -> None:
    recent_ids = set(db_session.scalars(select(ShotRow.id).order_by(ShotRow.id).limit(10)))
    for row in db_session.scalars(select(ShotRow).where(ShotRow.id.in_(recent_ids))):
        row.last_used_at = NOW - timedelta(days=1)
    db_session.flush()
    plans = plan(db_session, settings, n=2)
    assert len(plans) == 2
    assert not set(shot_ids(plans)) & recent_ids


def test_first_render_of_the_day_also_targets_tiktok(
    db_session: Session, settings: Settings, seeded: dict[str, int]
) -> None:
    plans = plan(db_session, with_cadence(settings, tiktok_renders_per_day=1))
    assert [render_plan.targets for render_plan in plans] == [
        (Platform.IG_TRIAL, Platform.TIKTOK),
        (Platform.IG_TRIAL,),
        (Platform.IG_TRIAL,),
        (Platform.IG_TRIAL,),
    ]


def test_no_tiktok_target_once_today_has_its_tiktok_render(
    db_session: Session, settings: Settings, fixed: tuple[Recipe, ...], seeded: dict[str, int]
) -> None:
    add_render(
        db_session, fixed[0], seeded["drop"], NOW - timedelta(hours=2), ["ig_trial", "tiktok"]
    )
    plans = plan(db_session, settings)
    assert [render_plan.targets for render_plan in plans] == [(Platform.IG_TRIAL,)] * 4


def test_the_day_boundary_is_in_the_owners_timezone(
    db_session: Session, settings: Settings, fixed: tuple[Recipe, ...], seeded: dict[str, int]
) -> None:
    # NOW is 06:00 in Los Angeles; this render was at 23:00 the previous local day.
    la_settings = settings.model_copy(
        update={"notify": settings.notify.model_copy(update={"timezone": "America/Los_Angeles"})}
    )
    add_render(
        db_session, fixed[0], seeded["drop"], NOW - timedelta(hours=7), ["ig_trial", "tiktok"]
    )
    assert plan(db_session, la_settings)[0].targets == (Platform.IG_TRIAL, Platform.TIKTOK)
    assert plan(db_session, settings)[0].targets == (Platform.IG_TRIAL,)


def test_tiktok_every_render_targets_tiktok_on_all(
    db_session: Session, settings: Settings, seeded: dict[str, int]
) -> None:
    plans = plan(db_session, with_cadence(settings, tiktok_every_render=True))
    both = (Platform.IG_TRIAL, Platform.TIKTOK)
    assert [render_plan.targets for render_plan in plans] == [both] * 4


def test_captions_use_the_template_per_target(
    db_session: Session, settings: Settings, seeded: dict[str, int]
) -> None:
    templates = {Platform.IG_TRIAL: "{song} · {snippet}", Platform.TIKTOK: "{song} #fyp"}
    captioned = settings.model_copy(
        update={"captions": settings.captions.model_copy(update={"templates": templates})}
    )
    first, second = plan(db_session, captioned, n=2)
    first_label = snippet_label(db_session, first)
    second_label = snippet_label(db_session, second)
    assert first.captions == {
        Platform.IG_TRIAL: f"{SONG_TITLE} · {first_label}",
        Platform.TIKTOK: f"{SONG_TITLE} #fyp",
    }
    assert second.captions == {Platform.IG_TRIAL: f"{SONG_TITLE} · {second_label}"}


def snippet_label(session: Session, render_plan: RenderPlan) -> str | None:
    return session.get_one(SnippetRow, render_plan.snippet_id).label


def test_recipes_at_their_weekly_limit_are_not_planned(
    db_session: Session, settings: Settings, fixed: tuple[Recipe, ...], seeded: dict[str, int]
) -> None:
    limit = settings.recipes.max_repeats_per_week
    for recipe in fixed[:2]:
        for day in range(limit):
            add_render(
                db_session, recipe, seeded["drop"], NOW - timedelta(days=day, hours=1), ["ig_trial"]
            )
    add_render(db_session, fixed[2], seeded["hook"], NOW - timedelta(days=8), ["ig_trial"])
    add_render(
        db_session,
        fixed[2],
        seeded["hook"],
        NOW - timedelta(hours=1),
        ["ig_trial"],
        RenderStatus.FAILED,
    )
    plans = plan(db_session, settings, n=5)
    assert [render_plan.recipe_id for render_plan in plans] == [fixed[2].id] * limit


def test_skips_recipes_the_library_cannot_fill_and_plans_the_rest(
    db_session: Session, settings: Settings, fixed: tuple[Recipe, ...], seeded: dict[str, int]
) -> None:
    for row in db_session.scalars(select(ShotRow).where(ShotRow.id > 11)):
        row.quality_score = 0.0
    db_session.flush()
    # 11 usable shots: enough for the downbeats recipe (10 slots), not the others (12 and 14).
    plans = plan(db_session, settings, n=4)
    assert [render_plan.recipe_id for render_plan in plans] == [fixed[2].id]


def test_returns_nothing_without_active_snippets(
    db_session: Session, settings: Settings, seeded: dict[str, int]
) -> None:
    for row in db_session.scalars(select(SnippetRow)):
        row.active = False
    db_session.flush()
    assert plan(db_session, settings) == []


def test_same_seed_gives_the_same_batch_and_nothing_is_written(
    db_session: Session, settings: Settings, seeded: dict[str, int]
) -> None:
    plans = plan(db_session, settings, seed=5)
    assert len(plans) == 4
    assert plan(db_session, settings, seed=5) == plans
    assert db_session.scalar(select(func.count()).select_from(RenderRow)) == 0
    assert db_session.scalar(select(func.count()).select_from(RecipeRow)) == 0
    assert db_session.scalar(select(func.max(ShotRow.last_used_at))) is None

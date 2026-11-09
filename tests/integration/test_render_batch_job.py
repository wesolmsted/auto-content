"""`run_render_batch` end to end on real ffmpeg: shots from `build_shots` on the portrait fixture,
a snippet analysed from the click track, renders, shot usage and hand-off notifications."""

import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import Settings, load_settings
from engine.db.enums import RenderStatus, SnippetType
from engine.db.models import FootageFileRow, RecipeRow, RenderRow, ShotRow
from engine.ingest.models import Shot
from engine.ingest.shot_rows import shot_to_row
from engine.ingest.shots import build_shots
from engine.recipes.models import CutRate, FootageCategory, Look, OnScreenText, OpeningShot, Recipe
from engine.render.batch_job import BatchSummary, run_render_batch
from engine.render.ffmpeg import validate
from engine.snippets.analysis import analyze_song, detect_drop
from engine.snippets.models import SongAnalysis
from engine.snippets.store import add_snippet, add_song
from engine.storage.media_store import LocalMediaStore
from tests.fakes.notify import FakeNotifier
from tests.fakes.vision import FakeVisionTagger

REPO_ROOT = Path(__file__).resolve().parents[2]
IN_POSTING_WINDOW = datetime(2026, 11, 8, 22, 30, tzinfo=UTC)  # 17:30 in New York
MORNING = datetime(2026, 11, 8, 14, 0, tzinfo=UTC)  # 09:00 in New York
FOOTAGE_FILES = 2
SNIPPET_START_S = 2.0
SNIPPET_END_S = 14.0

# Four 2 s downbeat slots in 7 s, so each 2.5 s portrait shot fits any slot.
RECIPE = Recipe(
    cut_rate=CutRate.DOWNBEATS,
    opening_shot=OpeningShot.MOST_MOTION,
    footage_category=FootageCategory.MIXED,
    length_s=7,
    on_screen_text=OnScreenText.NONE,
    snippet_type=SnippetType.DROP,
    look=Look.WARM,
)


@pytest.fixture(scope="module")
def base_settings() -> Settings:
    settings = load_settings(REPO_ROOT / "config", env_file=None)
    render = settings.render.model_copy(
        update={"preset": "ultrafast", "max_attempts": 1, "luts_dir": REPO_ROOT / "config/luts"}
    )
    recipes = settings.recipes.model_copy(update={"fixed": (RECIPE,)})
    return settings.model_copy(
        update={"render": render, "recipes": recipes, "handoff_base_url": "http://mac.tail.net/"}
    )


@pytest.fixture(scope="module")
def library(
    tmp_path_factory: pytest.TempPathFactory, portrait_scenes_clip: Path, base_settings: Settings
) -> list[Shot]:
    """Eight 2.5 s shots: the portrait fixture built as footage files 1 and 2."""
    store = LocalMediaStore(tmp_path_factory.mktemp("shot_media"))
    return [
        shot
        for footage_id in range(1, FOOTAGE_FILES + 1)
        for shot in build_shots(
            portrait_scenes_clip, footage_id, FakeVisionTagger(), store, base_settings.ingest
        )
    ]


@pytest.fixture(scope="module")
def song(click_track_wav: Path, base_settings: Settings) -> SongAnalysis:
    return analyze_song(click_track_wav, base_settings.snippets)


@pytest.fixture
def seeded(
    db_session: Session,
    library: list[Shot],
    song: SongAnalysis,
    click_track_wav: Path,
    base_settings: Settings,
) -> None:
    db_session.add_all(
        FootageFileRow(drive_file_id=f"drive-{index}", name=f"clip{index}.mp4")
        for index in range(1, FOOTAGE_FILES + 1)
    )
    db_session.flush()
    db_session.add_all(shot_to_row(shot) for shot in library)
    song_id = add_song(db_session, click_track_wav, song)
    snippet_settings = base_settings.snippets
    drop_s = detect_drop(click_track_wav, song, SNIPPET_START_S, SNIPPET_END_S, snippet_settings)
    add_snippet(
        db_session, song_id, SnippetType.DROP, SNIPPET_START_S, SNIPPET_END_S, drop_s, "drop"
    )
    db_session.commit()


def run_batch(
    session: Session,
    settings: Settings,
    tmp_path: Path,
    notifier: FakeNotifier | None,
    now: datetime,
) -> BatchSummary:
    store = LocalMediaStore(tmp_path / "media")
    return run_render_batch(session, settings, store, notifier, random.Random(3), now, 2)


def renders(session: Session) -> list[RenderRow]:
    session.expire_all()
    return list(session.scalars(select(RenderRow).order_by(RenderRow.id)))


def use_counts(session: Session) -> dict[int, int]:
    return {row.id: row.use_count for row in session.scalars(select(ShotRow))}


@pytest.mark.usefixtures("seeded")
def test_a_batch_in_the_posting_window_renders_records_usage_and_notifies(
    db_session: Session, base_settings: Settings, tmp_path: Path
) -> None:
    notifier = FakeNotifier()

    summary = run_batch(db_session, base_settings, tmp_path, notifier, IN_POSTING_WINDOW)

    assert summary == BatchSummary(planned=2, ready=2, failed=0, notified=2)
    rows = renders(db_session)
    assert [row.status for row in rows] == [RenderStatus.READY, RenderStatus.READY]
    for row in rows:
        assert row.file_path is not None
        assert row.duration_s is not None
        validate(Path(row.file_path), row.duration_s, base_settings.render)
        assert row.notified_at == IN_POSTING_WINDOW

    recipe_row = db_session.get_one(RecipeRow, RECIPE.id)
    assert recipe_row.params_json == RECIPE.canonical_params()
    assert recipe_row.source == "generated"

    used = {shot_id for row in rows for shot_id in row.shot_ids_json}
    assert len(used) == 8
    assert use_counts(db_session) == dict.fromkeys(used, 1)
    for shot in db_session.scalars(select(ShotRow)):
        assert shot.last_used_at == IN_POSTING_WINDOW

    assert [sent.click_url for sent in notifier.sent] == [
        f"http://mac.tail.net/handoff/{row.id}" for row in rows
    ]


@pytest.mark.usefixtures("seeded")
def test_a_morning_batch_holds_its_notifications(
    db_session: Session, base_settings: Settings, tmp_path: Path
) -> None:
    notifier = FakeNotifier()

    summary = run_batch(db_session, base_settings, tmp_path, notifier, MORNING)

    assert summary == BatchSummary(planned=2, ready=2, failed=0, notified=0)
    assert notifier.attempted == []
    assert [(row.status, row.notified_at) for row in renders(db_session)] == [
        (RenderStatus.READY, None),
        (RenderStatus.READY, None),
    ]


@pytest.mark.usefixtures("seeded")
def test_a_failed_render_does_not_stop_the_batch_or_count_its_shots_as_used(
    db_session: Session, base_settings: Settings, tmp_path: Path
) -> None:
    broken = db_session.scalars(select(ShotRow).order_by(ShotRow.id)).first()
    assert broken is not None
    broken.proxy_path = str(tmp_path / "missing.mp4")
    db_session.commit()
    notifier = FakeNotifier()

    summary = run_batch(db_session, base_settings, tmp_path, notifier, IN_POSTING_WINDOW)

    assert summary == BatchSummary(planned=2, ready=1, failed=1, notified=1)
    rows = renders(db_session)
    [failed] = [row for row in rows if row.status is RenderStatus.FAILED]
    [ready] = [row for row in rows if row.status is RenderStatus.READY]
    assert broken.id in failed.shot_ids_json
    assert use_counts(db_session) == {
        **dict.fromkeys(failed.shot_ids_json, 0),
        **dict.fromkeys(ready.shot_ids_json, 1),
    }
    assert [sent.click_url for sent in notifier.sent] == [f"http://mac.tail.net/handoff/{ready.id}"]


@pytest.mark.usefixtures("seeded")
def test_without_a_notifier_renders_are_made_and_left_pending(
    db_session: Session, base_settings: Settings, tmp_path: Path
) -> None:
    summary = run_batch(db_session, base_settings, tmp_path, None, IN_POSTING_WINDOW)

    assert summary == BatchSummary(planned=2, ready=2, failed=0, notified=0)
    assert [row.notified_at for row in renders(db_session)] == [None, None]


@pytest.mark.usefixtures("seeded")
def test_a_rerun_reuses_the_recipe_row(
    db_session: Session, base_settings: Settings, tmp_path: Path
) -> None:
    no_cooldown = base_settings.model_copy(
        update={"recipes": base_settings.recipes.model_copy(update={"shot_cooldown_days": 0})}
    )
    run_batch(db_session, no_cooldown, tmp_path, None, MORNING)
    summary = run_batch(db_session, no_cooldown, tmp_path, None, MORNING)

    assert summary.ready == 1
    assert db_session.scalars(select(RecipeRow.id)).all() == [RECIPE.id]
    assert sum(use_counts(db_session).values()) == 3 * 4

import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.config import Settings, load_settings
from engine.db.enums import SnippetType
from engine.db.models import FootageFileRow, SnippetRow, SongRow
from engine.recipes.batch import plan_batch
from tests.integration.test_recipes_batch import make_shot_row

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 8, 14, 0, tzinfo=UTC)
GRID_CUTS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]


@pytest.fixture
def settings() -> Settings:
    """Only the every_2_beats / hard cut on drop / 12 s recipe."""
    settings = load_settings(REPO_ROOT / "config", env_file=None)
    recipes = settings.recipes.model_copy(update={"fixed": settings.recipes.fixed[:1]})
    return settings.model_copy(update={"recipes": recipes})


def seed_drop_snippet(session: Session, drop_s: float | None) -> None:
    beats = [index * 0.5 for index in range(120)]
    song = SongRow(
        title="Night Drive",
        audio_path="songs/night_drive.wav",
        duration_s=60.0,
        bpm=120.0,
        beats_json=beats,
        downbeats_json=beats[::4],
        analyzed_at=NOW,
    )
    footage = FootageFileRow(drive_file_id="drive-1", name="clip.mp4")
    session.add_all([song, footage])
    session.flush()
    session.add(
        SnippetRow(
            song_id=song.id, snippet_type=SnippetType.DROP, start_s=10.0, end_s=32.0, drop_s=drop_s
        )
    )
    session.add_all(make_shot_row(footage.id, index) for index in range(20))
    session.flush()


@pytest.mark.parametrize(
    ("drop_s", "expected_cuts"),
    [
        (40.0, GRID_CUTS),
        (5.0, GRID_CUTS),
        (None, GRID_CUTS),
        (15.05, [1.0, 2.0, 3.0, 4.0, 5.05, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]),
    ],
    ids=["drop after window", "drop before window", "no drop", "drop inside window"],
)
def test_a_drop_outside_the_snippet_window_plans_with_no_drop_cut(
    db_session: Session, settings: Settings, drop_s: float | None, expected_cuts: list[float]
) -> None:
    seed_drop_snippet(db_session, drop_s)

    [render_plan] = plan_batch(db_session, settings, random.Random(1), NOW, 1)

    cuts = [segment.output_start_s for segment in render_plan.timeline.segments[1:]]
    assert cuts == pytest.approx(expected_cuts)

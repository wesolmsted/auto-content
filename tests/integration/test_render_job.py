from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.config import CaptionSettings, Settings, load_settings
from engine.db.enums import Platform, RenderStatus, SnippetType
from engine.db.models import RecipeRow, RenderRow, SnippetRow, SongRow
from engine.recipes.models import Look, RenderPlan, Segment, Timeline
from engine.render.ffmpeg import validate
from engine.render.job import execute_plan
from engine.storage.media_store import LocalMediaStore

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings() -> Settings:
    settings = load_settings(REPO_ROOT / "config", env_file=None)
    render = settings.render.model_copy(
        update={
            "preset": "ultrafast",
            "max_attempts": 2,
            "luts_dir": REPO_ROOT / "config" / "luts",
            "font_file": REPO_ROOT / "engine" / "render" / "fonts" / "Anton-Regular.ttf",
        }
    )
    captions = CaptionSettings(
        templates={Platform.IG_TRIAL: "{song} 🌃", Platform.TIKTOK: "{song} #fyp"}
    )
    return settings.model_copy(update={"render": render, "captions": captions})


@pytest.fixture
def snippet_id(db_session: Session, click_track_wav: Path) -> int:
    db_session.add(RecipeRow(id="rcp_000000000001", params_json={}))
    song = SongRow(title="Night Drive", audio_path=str(click_track_wav))
    db_session.add(song)
    db_session.flush()
    snippet = SnippetRow(song_id=song.id, snippet_type=SnippetType.DROP, start_s=8.0, end_s=10.0)
    db_session.add(snippet)
    db_session.commit()
    return snippet.id


def make_plan(snippet_id: int, source: Path) -> RenderPlan:
    timeline = Timeline(
        duration_s=1.5,
        look=Look.BW,
        segments=(
            Segment(
                shot_id=7,
                source_path=source,
                source_in_s=0.0,
                source_out_s=1.5,
                output_start_s=0.0,
                output_end_s=1.5,
            ),
        ),
    )
    return RenderPlan(
        recipe_id="rcp_000000000001",
        snippet_id=snippet_id,
        timeline=timeline,
        targets=(Platform.IG_TRIAL, Platform.TIKTOK),
        captions={},
    )


def test_a_successful_render_is_stored_ready_with_everything_needed_to_reproduce_it(
    db_session: Session,
    tmp_path: Path,
    settings: Settings,
    snippet_id: int,
    landscape_motion_clip: Path,
) -> None:
    store = LocalMediaStore(tmp_path / "media")
    plan = make_plan(snippet_id, landscape_motion_clip)

    render_id = execute_plan(db_session, plan, store, settings)

    row = db_session.get(RenderRow, render_id)
    assert row is not None
    assert row.status is RenderStatus.READY
    assert row.attempts == 1
    assert row.error is None
    assert row.file_path is not None
    assert Path(row.file_path).parent == tmp_path.resolve() / "media" / "renders"
    validate(Path(row.file_path), 1.5, settings.render)
    assert row.duration_s == pytest.approx(1.5)
    assert Timeline.model_validate(row.timeline_json) == plan.timeline
    assert row.shot_ids_json == [7]
    assert row.ffmpeg_argv_json[0] == "ffmpeg"
    assert row.ffmpeg_argv_json[-1] == row.file_path
    assert row.targets_json == ["ig_trial", "tiktok"]
    assert row.captions_json == {"ig_trial": "Night Drive 🌃", "tiktok": "Night Drive #fyp"}


def test_a_render_that_keeps_failing_is_retried_then_marked_failed(
    db_session: Session,
    tmp_path: Path,
    settings: Settings,
    snippet_id: int,
) -> None:
    store = LocalMediaStore(tmp_path / "media")
    plan = make_plan(snippet_id, tmp_path / "missing_proxy.mp4")

    render_id = execute_plan(db_session, plan, store, settings)

    db_session.expire_all()
    row = db_session.get(RenderRow, render_id)
    assert row is not None
    assert row.status is RenderStatus.FAILED
    assert row.attempts == 2
    assert row.error is not None
    assert "missing_proxy.mp4" in row.error
    assert row.file_path is None
    assert str(tmp_path / "missing_proxy.mp4") in row.ffmpeg_argv_json
    assert row.timeline_json == plan.timeline.model_dump(mode="json")

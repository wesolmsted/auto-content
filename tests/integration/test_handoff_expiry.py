from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.config import load_settings
from engine.db.enums import RenderStatus, SnippetType
from engine.db.models import RecipeRow, RenderRow, SnippetRow, SongRow
from engine.handoff.expiry import supersede_expired

REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
BASE_SETTINGS = load_settings(REPO_CONFIG_DIR, env_file=None)
SETTINGS = BASE_SETTINGS.model_copy(
    update={"handoff": BASE_SETTINGS.handoff.model_copy(update={"expire_hours": 72})}
)
RECIPE = BASE_SETTINGS.recipes.fixed[0]
NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)


def add_render(session: Session, status: RenderStatus, created_at: datetime) -> int:
    if session.get(RecipeRow, RECIPE.id) is None:
        session.add(RecipeRow(id=RECIPE.id, params_json=RECIPE.canonical_params()))
    song = SongRow(title="Song", audio_path="song.wav")
    session.add(song)
    session.flush()
    snippet = SnippetRow(song_id=song.id, snippet_type=SnippetType.DROP, start_s=0, end_s=7)
    session.add(snippet)
    session.flush()
    render = RenderRow(
        recipe_id=RECIPE.id,
        snippet_id=snippet.id,
        timeline_json={},
        status=status,
        created_at=created_at,
    )
    session.add(render)
    session.flush()
    return render.id


def statuses(session: Session) -> dict[int, RenderStatus]:
    rows = session.execute(select(RenderRow.id, RenderRow.status)).all()
    return {render_id: status for render_id, status in rows}


def test_supersedes_only_ready_renders_older_than_expire_hours(db_session: Session) -> None:
    expired = add_render(db_session, RenderStatus.READY, NOW - timedelta(hours=72, seconds=1))
    at_limit = add_render(db_session, RenderStatus.READY, NOW - timedelta(hours=72))
    fresh = add_render(db_session, RenderStatus.READY, NOW - timedelta(hours=1))
    old_approved = add_render(db_session, RenderStatus.APPROVED, NOW - timedelta(days=10))
    old_rejected = add_render(db_session, RenderStatus.REJECTED, NOW - timedelta(days=10))
    old_rendering = add_render(db_session, RenderStatus.RENDERING, NOW - timedelta(days=10))

    superseded = supersede_expired(db_session, SETTINGS, NOW)

    assert superseded == 1
    assert statuses(db_session) == {
        expired: RenderStatus.SUPERSEDED,
        at_limit: RenderStatus.READY,
        fresh: RenderStatus.READY,
        old_approved: RenderStatus.APPROVED,
        old_rejected: RenderStatus.REJECTED,
        old_rendering: RenderStatus.RENDERING,
    }


def test_uses_the_configured_expiry(db_session: Session) -> None:
    render = add_render(db_session, RenderStatus.READY, NOW - timedelta(hours=3))
    short = SETTINGS.model_copy(
        update={"handoff": SETTINGS.handoff.model_copy(update={"expire_hours": 2})}
    )

    assert supersede_expired(db_session, short, NOW) == 1
    assert statuses(db_session) == {render: RenderStatus.SUPERSEDED}
    assert supersede_expired(db_session, short, NOW) == 0

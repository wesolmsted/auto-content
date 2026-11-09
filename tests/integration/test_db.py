from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from engine.db.enums import (
    FootageStatus,
    Orientation,
    Platform,
    PostStatus,
    RenderStatus,
    SnippetType,
)
from engine.db.migrate import upgrade_to_head
from engine.db.models import (
    Base,
    FootageFileRow,
    PostRow,
    RecipeRow,
    RenderRow,
    ShotRow,
    SnippetRow,
    SongRow,
)
from engine.db.session import create_db_engine


def test_upgrade_on_empty_sqlite_creates_every_model_table(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'empty.sqlite3'}"
    upgrade_to_head(url)
    engine = create_db_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert tables == set(Base.metadata.tables) | {"alembic_version"}
    assert {"footage_files", "variable_scores", "style_references", "metric_imports"} <= tables


def test_migrations_match_the_models_exactly(db_url: str) -> None:
    engine = create_db_engine(db_url)
    with engine.connect() as connection:
        differences = compare_metadata(
            MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata
        )
    engine.dispose()
    assert differences == []


def test_upgrade_twice_is_a_no_op(db_url: str) -> None:
    upgrade_to_head(db_url)
    engine = create_db_engine(db_url)
    with engine.connect() as connection:
        versions = connection.execute(text("select count(*) from alembic_version")).scalar_one()
    engine.dispose()
    assert versions == 1


def add_render(session: Session) -> RenderRow:
    song = SongRow(title="Song", audio_path="song.wav")
    session.add(song)
    session.flush()
    snippet = SnippetRow(song_id=song.id, snippet_type=SnippetType.DROP, start_s=10.0, end_s=25.0)
    recipe = RecipeRow(id="rcp_0123456789ab", params_json={"look": "warm"})
    session.add_all([snippet, recipe])
    session.flush()
    render = RenderRow(recipe_id=recipe.id, snippet_id=snippet.id, timeline_json={})
    session.add(render)
    session.flush()
    return render


def test_datetimes_are_read_back_as_aware_utc(db_session: Session) -> None:
    eastern = timezone(timedelta(hours=-5))
    footage = FootageFileRow(drive_file_id="f1", name="a.mp4")
    db_session.add(footage)
    db_session.flush()
    footage.updated_at = datetime(2026, 11, 8, 7, 0, tzinfo=eastern)
    db_session.commit()
    db_session.expire_all()

    reloaded = db_session.get(FootageFileRow, footage.id)
    assert reloaded is not None
    assert reloaded.updated_at == datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    assert reloaded.updated_at.tzinfo is UTC
    assert reloaded.created_at.tzinfo is UTC


def test_naive_datetimes_are_rejected(db_session: Session) -> None:
    db_session.add(
        FootageFileRow(drive_file_id="f1", name="a.mp4", created_at=datetime(2026, 1, 1))
    )
    with pytest.raises(StatementError):
        db_session.flush()


def test_status_enums_round_trip_and_unknown_values_are_rejected(db_session: Session) -> None:
    footage = FootageFileRow(drive_file_id="f1", name="a.mp4")
    db_session.add(footage)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(FootageFileRow, footage.id).status is FootageStatus.NEW  # type: ignore[union-attr]

    stored = db_session.execute(text("select status from footage_files")).scalar_one()
    assert stored == "new"

    db_session.add(FootageFileRow(drive_file_id="f2", name="b.mp4", status="donee"))
    with pytest.raises(StatementError):
        db_session.flush()


def test_foreign_keys_are_enforced_on_sqlite(db_session: Session) -> None:
    db_session.add(
        ShotRow(
            footage_file_id=999,
            start_s=0.0,
            end_s=1.0,
            duration_s=1.0,
            width=1080,
            height=1920,
            fps=30.0,
            orientation=Orientation.PORTRAIT,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_one_post_per_render_and_platform(db_session: Session) -> None:
    render = add_render(db_session)
    assert render.status is RenderStatus.RENDERING
    db_session.add(PostRow(render_id=render.id, platform=Platform.IG_TRIAL))
    db_session.add(PostRow(render_id=render.id, platform=Platform.TIKTOK))
    db_session.flush()
    assert {post.status for post in db_session.query(PostRow)} == {PostStatus.AWAITING_POST}

    db_session.add(PostRow(render_id=render.id, platform=Platform.IG_TRIAL))
    with pytest.raises(IntegrityError):
        db_session.flush()

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.db.enums import Orientation
from engine.db.models import FootageFileRow, ShotRow
from engine.ingest.models import QualityIssue, Shot, ShotTags
from engine.ingest.shot_rows import shot_from_row, shot_to_row


def make_shot(footage_file_id: int, **overrides: object) -> Shot:
    values: dict[str, object] = {
        "footage_file_id": footage_file_id,
        "start_s": 12.5,
        "end_s": 17.25,
        "width": 1280,
        "height": 720,
        "fps": 29.97,
        "orientation": Orientation.LANDSCAPE,
        "motion_score": 0.61,
        "quality_score": 0.55,
        "tags": ShotTags(
            category="nature",
            subject="landscape",
            setting="beach",
            lighting="golden_hour",
            mood="calm",
            has_face=True,
            quality=0.9,
            issues=(QualityIssue.SHAKY, QualityIssue.DULL),
        ),
        "proxy_path": Path("/data/media/shots/7.mp4"),
        "thumbnail_path": Path("/data/media/shots/7.jpg"),
        "use_count": 3,
        "last_used_at": datetime(2026, 11, 6, 9, 30, tzinfo=UTC),
    }
    return Shot.model_validate(values | overrides)


@pytest.mark.parametrize(
    "overrides",
    [{}, {"thumbnail_path": None, "last_used_at": None, "use_count": 0}],
)
def test_a_stored_shot_reads_back_unchanged(
    db_session: Session, overrides: dict[str, object]
) -> None:
    footage = FootageFileRow(drive_file_id="drive-1", name="clip.mp4")
    db_session.add(footage)
    db_session.flush()
    shot = make_shot(footage.id, **overrides)
    db_session.add(shot_to_row(shot))
    db_session.commit()
    db_session.expunge_all()

    row = db_session.query(ShotRow).one()
    assert shot_from_row(row) == shot.model_copy(update={"id": row.id})
